"""Local UI for the model.

  python3 ui.py        -> open http://localhost:8765

Endpoints:
  GET  /                         the page (ui.html)
  GET  /api/slate?date=&teaser=  evaluate a slate
  GET  /api/teams                Sagarin ratings + HFA
  GET  /api/lines?date=          Line Tracking: DK spread + edge over the week
  GET  /api/parlays?min=        Parlays: 2-3 leg groups of high-confidence teaser picks
  GET  /api/history              frozen results from history.json (see snapshot.py)
  GET  /api/matchup?home=&away=&spread=&neutral=&teaser=   one-off calculator
  POST /api/save?date=&teaser=   append the slate to picks/YYYYMMDD.csv
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import confidence
import linetrack
import odds
import parlays
import run
import sagarin
import snapshot
import team_lines
from model import TEASER_POINTS, Game, evaluate

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8765))
# Render sets RENDER=true on its servers. Set PUBLIC=1 for any other host.
PUBLIC = bool(os.environ.get("RENDER") or os.environ.get("PUBLIC"))
HOST = "0.0.0.0" if PUBLIC else "127.0.0.1"
# Saving to CSV is local-only: public hosts have throwaway disks, and
# visitors shouldn't be able to write files.
ALLOW_SAVE = not PUBLIC
_cache = {"at": 0.0, "ratings": None}


def ratings(force: bool = False) -> sagarin.SagarinRatings:
    # Sagarin updates weekly; re-fetch at most every 10 minutes.
    if force or not _cache["ratings"] or time.time() - _cache["at"] > 600:
        _cache["ratings"], _cache["at"] = sagarin.load(), time.time()
    return _cache["ratings"]


def pick_json(p, line=None) -> dict:
    g = p.game
    d = {
        "home": g.home, "away": g.away, "neutral": g.neutral,
        "home_rating": g.home_rating, "away_rating": g.away_rating,
        "dk_home_spread": g.dk_home_spread, "dk_home_ml": g.dk_home_ml, "dk_away_ml": g.dk_away_ml,
        "projected": p.projected, "edge": p.edge, "action": p.action, "side": p.side,
        "line": p.line, "odds": p.odds, "teaser_line": p.teaser_line, "flags": p.flags,
        "result": p.result, "teaser_result": p.teaser_result,
    }
    if line is not None:
        d.update(kickoff=line.kickoff, status=line.status, state=line.state,
                 completed=line.completed, home_score=line.home_score,
                 away_score=line.away_score, line_source=line.line_source, dk_total=line.total,
                 home_rank=line.home_rank, away_rank=line.away_rank)
    return d


def slate_json(day: str, teaser: float, refresh: bool) -> dict:
    s = run.build(day, teaser, ratings(force=refresh))
    return {
        "date": day, "as_of": s.ratings.as_of, "hfa": s.ratings.home_advantage, "teaser": teaser,
        "games": [pick_json(p, l) for l, p in s.picks],
        "no_line": [{"home": l.home, "away": l.away, "kickoff": l.kickoff, "status": l.status,
                     "state": l.state, "home_score": l.home_score, "away_score": l.away_score,
                     "home_rank": l.home_rank, "away_rank": l.away_rank}
                    for l in s.no_line],
        "unmatched": s.unmatched,
        "can_save": ALLOW_SAVE,
    }


_past_slates = {}  # day -> Slate, for days before today (every game final)


week_window = linetrack.week_window


def week_json(day: str) -> dict:
    """Every finished game Tue-Sat of the week: frozen history first, then
    finals the snapshot hasn't recorded yet (same rules as the History tab)."""
    days = week_window(day)
    today = snapshot.eastern_today().strftime("%Y%m%d")
    rows = [h for h in snapshot.load_history() if h["date"] in days]
    seen = {h["game_id"] for h in rows}
    r = ratings()
    for d in days:
        if d > today:
            break
        s = _past_slates.get(d)
        if s is None or s.ratings.as_of != r.as_of:
            s = run.build(d, snapshot.TEASER, r)
            if d < today:
                _past_slates[d] = s
        if snapshot.dates_ok(s):
            rows += [dict(x, provisional=True) for x in snapshot.finished_rows(s, None)
                     if x["game_id"] not in seen]
    return {"week": odds.week_number(days[0]) or odds.week_number(day),
            "start": days[0], "end": days[-1], "games": rows}


_live_slates = {}  # day -> (built_at, Slate) for today and later; rebuilt every 2 min


upcoming_week = linetrack.upcoming_week


def week_slates(days):
    """The slate for each of `days`.
    Past days are cached for good; today and later for 2 minutes."""
    r = ratings()
    today = snapshot.eastern_today().strftime("%Y%m%d")

    def one(d):
        if d < today:
            s = _past_slates.get(d)
            if s is None or s.ratings.as_of != r.as_of:
                s = _past_slates[d] = run.build(d, snapshot.TEASER, r)
            return s
        at, s = _live_slates.get(d, (0.0, None))
        if s is None or s.ratings.as_of != r.as_of or time.time() - at > 120:
            s = run.build(d, snapshot.TEASER, r)
            _live_slates[d] = (time.time(), s)
        return s

    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(one, days))


def confidence_json(day: str) -> dict:
    """One confidence score per team (see confidence.py):
    team half = record against its own DK spread teased 3, every finished game
    this season (History + archive/extra_games.json); edge half = edge range of
    its game THIS WEEK (Tue-Sat; on Sun/Mon the coming week), any pick type.
    A team that already played this week or is on a bye uses its most recent
    game with an edge."""
    hist = history_json()["games"]
    games = confidence.team_games(hist + team_lines.load())
    records = {t: confidence._record(rows) for t, rows in games.items()}
    edges = confidence.edge_table(hist)
    divs = {t.team: t.division for t in ratings().teams.values()}
    week = {}  # team -> its game this week (next unfinished, else the latest)
    days = upcoming_week(day)
    for s in week_slates(days):
        for l, p in s.picks:
            g = pick_json(p, l)
            for team in (p.game.home, p.game.away):
                home = team == p.game.home
                own = g["dk_home_spread"] if home else -g["dk_home_spread"]
                mine = dict(g, team_side=team, opponent=p.game.away if home else p.game.home, is_home=home,
                            own_line=own, own_teased=own + confidence.TEASER,
                            own_result=(confidence.own_result((g["home_score"] - g["away_score"]) * (1 if home else -1), own)
                                        if g["state"] == "post" and g["home_score"] is not None else None))
                prev = week.get(team)
                if prev is None or (prev["state"] == "post" and
                                    (g["state"] != "post" or g["kickoff"] > prev["kickoff"])):
                    week[team] = mine
    rows = []
    for team in sorted(set(records) | set(week)):
        t = week.get(team)
        past = games.get(team, [])
        last_edge = next((r for r in reversed(past) if r.get("edge") is not None), None)
        if t is not None and t["state"] != "post":
            edge, edge_from = t["edge"], "week"
        elif t is not None:      # already played this week: that game
            edge, edge_from = t["edge"], "last"
        elif last_edge:          # bye or no line yet: most recent game with an edge
            edge, edge_from = last_edge["edge"], "last"
        else:
            edge, edge_from = None, None
        ep = confidence.edge_points(edge, edges)
        rec = records.get(team)
        played = (rec["w"] + rec["l"] + rec["p"]) if rec else 0
        scored = played >= confidence.MIN_GAMES
        team_pts = rec["points"] if scored else None
        last = past[-1] if past else None
        rows.append({
            "team": team, "division": divs.get(team), "record": rec, "team_pts": team_pts, "games": played,
            "edge": edge, "edge_from": edge_from, **ep,
            "confidence": round(team_pts + ep["edge_pts"], 1) if scored else None,
            "game": t,
            "recent": [r["teaser_result"] for r in past[-5:]],
            "last": {k: last[k] for k in ("opponent", "home", "date", "line", "teased_line", "teaser_result")} if last else None,
        })
    # Scored teams first (highest first), then teams still short of the minimum.
    rows.sort(key=lambda r: (r["confidence"] is None, -(r["confidence"] or 0),
                             -(r["record"]["w"] if r["record"] else 0), r["team"]))
    return {"date": day, "week": odds.week_number(days[0]) or odds.week_number(day),
            "start": days[0], "end": days[-1], "teaser": snapshot.TEASER, "min_games": confidence.MIN_GAMES,
            "teams": rows, "edges": edges,
            "graded": sum(len(v) for v in games.values()) // 2}


def lines_json(day: str) -> dict:
    """Every game of the coming week (Tue-Sat; on Sun/Mon the week ahead) with
    its tracked points from lines.json: one per night, close, plus a live
    'now' point for games that haven't kicked off."""
    days = upcoming_week(day)
    data = linetrack.load()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    games = []
    for s in week_slates(days):
        for l, p in s.picks:
            e = data.get(l.game_id) or {}
            g = pick_json(p, l)
            close = e.get("close")
            if close is None and l.state != "pre":
                close = dict(linetrack._point(p), at=l.kickoff)
            games.append(dict(g, game_id=l.game_id, date=s.day,
                              points=e.get("points", []), close=close,
                              now=dict(linetrack._point(p), at=now) if l.state == "pre" else None))
    games.sort(key=lambda g: (g["kickoff"] or "", g["away"]))
    return {"date": day, "week": odds.week_number(days[0]) or odds.week_number(day),
            "start": days[0], "end": days[-1], "as_of": ratings().as_of, "games": games}


def parlays_json(day: str, min_conf: float) -> dict:
    """Every parlay this season (backtest with as-of confidence) plus this
    week's upcoming ones. See parlays.py."""
    hist = history_json()["games"]
    asof = parlays.AsOf(hist, team_lines.load())
    core = lambda r: r["action"] == "SPREAD" and "DISAGREE" not in (r.get("flags") or [])
    cands, seen = [], set()
    for r in hist:
        if core(r) and r.get("teaser_result"):
            seen.add(r["game_id"])
            cands.append({"game_id": r["game_id"], "date": r["date"], "kickoff": r["kickoff"],
                          "team": r["side"], "opponent": r["away"] if r["side"] == r["home"] else r["home"],
                          "home": r["side"] == r["home"], "teased_line": r["teaser_line"], "edge": r["edge"],
                          "result": r["teaser_result"], "state": "post",
                          "home_name": r["home"], "away_name": r["away"],
                          "home_score": r["home_score"], "away_score": r["away_score"],
                          "home_rank": r.get("home_rank"), "away_rank": r.get("away_rank"),
                          "dk_total": r.get("dk_total")})
    for s in week_slates(upcoming_week(day)):
        for l, p in s.picks:
            r = pick_json(p, l)
            if not core(r) or l.game_id in seen:
                continue
            cands.append({"game_id": l.game_id, "date": s.day, "kickoff": l.kickoff,
                          "team": p.side, "opponent": p.game.away if p.side == p.game.home else p.game.home,
                          "home": p.side == p.game.home, "teased_line": p.teaser_line, "edge": p.edge,
                          "result": p.teaser_result if l.completed else None, "state": l.state,
                          "home_name": p.game.home, "away_name": p.game.away,
                          "home_score": l.home_score, "away_score": l.away_score,
                          "home_rank": l.home_rank, "away_rank": l.away_rank, "dk_total": l.total})
    out = parlays.build(cands, asof, min_conf)
    return {"date": day, "min_conf": min_conf, "teaser": snapshot.TEASER, "parlays": out}


def history_json() -> dict:
    """Frozen history plus today's/yesterday's finals the scheduled snapshot
    hasn't recorded yet (marked provisional until it does)."""
    rows = snapshot.load_history()
    seen = {h["game_id"] for h in rows}
    pending = []
    try:
        r = ratings()
        for day in snapshot.default_days():
            s = run.build(day, snapshot.TEASER, r)
            if snapshot.dates_ok(s):
                pending += [dict(x, provisional=True) for x in snapshot.finished_rows(s, None)
                            if x["game_id"] not in seen]
    except Exception as e:  # history still works if ESPN is down
        print("history: live finals unavailable:", e, flush=True)
    return {"games": rows + pending}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _q(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _route(self, method):
        path, q = urlparse(self.path).path, self._q()
        day = q.get("date") or date.today().strftime("%Y%m%d")
        teaser = float(q.get("teaser") or TEASER_POINTS)
        try:
            if method == "GET" and path == "/":
                with open(os.path.join(HERE, "ui.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if method == "GET" and path == "/api/slate":
                return self._send(200, slate_json(day, teaser, q.get("refresh") == "1"))
            if method == "GET" and path == "/api/confidence":
                return self._send(200, confidence_json(day))
            if method == "GET" and path == "/api/week":
                return self._send(200, week_json(day))
            if method == "GET" and path == "/api/lines":
                return self._send(200, lines_json(day))
            if method == "GET" and path == "/api/parlays":
                return self._send(200, parlays_json(day, float(q.get("min") or 65)))
            if method == "GET" and path == "/api/history":
                return self._send(200, history_json())
            if method == "GET" and path == "/api/teams":
                r = ratings()
                return self._send(200, {"as_of": r.as_of, "hfa": r.home_advantage, "teams": [
                    {"team": t.team, "rating": t.rating, "rank": t.rank, "division": t.division,
                     "record": f"{t.wins}-{t.losses}"}
                    for t in sorted(r.teams.values(), key=lambda t: t.rank)]})
            if method == "GET" and path == "/api/matchup":
                r = ratings()
                h, a = r.get(q.get("home", "")), r.get(q.get("away", ""))
                if not h or not a:
                    return self._send(400, {"error": "Pick two teams from the list"})
                g = Game(home=h.team, away=a.team, home_rating=h.rating, away_rating=a.rating,
                         dk_home_spread=float(q.get("spread") or 0), hfa=r.home_advantage,
                         neutral=q.get("neutral") == "1",
                         dk_home_ml=int(q["ml"]) if q.get("ml") else None)
                return self._send(200, pick_json(evaluate(g, teaser)))
            if method == "POST" and path == "/api/save":
                if not ALLOW_SAVE:
                    return self._send(403, {"error": "Saving is turned off on the public site"})
                s = run.build(day, teaser, ratings())
                return self._send(200, {"path": run.save_csv(s), "rows": len(s.picks)})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # surface errors in the page
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"CFB model UI: http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
