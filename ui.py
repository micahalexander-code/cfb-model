"""Local UI for the model.

  python3 ui.py        -> open http://localhost:8765

Endpoints:
  GET  /                         the page (ui.html)
  GET  /api/slate?date=&teaser=  evaluate a slate
  GET  /api/teams                Sagarin ratings + HFA
  GET  /api/history              frozen results from history.json (see snapshot.py)
  GET  /api/matchup?home=&away=&spread=&neutral=&teaser=   one-off calculator
  POST /api/save?date=&teaser=   append the slate to picks/YYYYMMDD.csv
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import confidence
import odds
import run
import sagarin
import snapshot
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
                 away_score=line.away_score, line_source=line.line_source,
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


def week_window(day: str):
    """Tuesday..Saturday of the CFB week containing `day`. Sunday/Monday belong
    to the week that just ended."""
    d = datetime.strptime(day, "%Y%m%d").date()
    tue = d - timedelta(days=(d.weekday() - 1) % 7)
    return [(tue + timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]


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


def upcoming_week(day: str):
    """Tue..Sat of the week whose games are next: on Sunday/Monday that's the
    coming week, otherwise the week containing `day`."""
    d = datetime.strptime(day, "%Y%m%d").date()
    if d.weekday() in (6, 0):  # Sunday, Monday
        d += timedelta(days=(1 - d.weekday()) % 7)
    return week_window(d.strftime("%Y%m%d"))


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
    """One confidence score per team: its record against our teased line in
    every finished game, plus the edge-range points of its matchup THIS WEEK
    (Tue-Sat; on Sunday/Monday the coming week). Only a team that has already played this week,
    or is on a bye, falls back to its most recent finished game."""
    hist = history_json()["games"]
    games = confidence.team_games(hist)
    records = {t: confidence._record(rows) for t, rows in games.items()}
    edges = confidence.edge_table(hist)
    week = {}  # team -> its Core-Model-style (SPREAD) game this week
    days = upcoming_week(day)
    for s in week_slates(days):
        for l, p in s.picks:
            if p.action != "SPREAD":
                continue
            g = pick_json(p, l)
            for team in (p.game.home, p.game.away):
                prev = week.get(team)
                # Keep the team's next unfinished game; otherwise its latest one.
                if prev is None or (prev["state"] == "post" and
                                    (g["state"] != "post" or g["kickoff"] > prev["kickoff"])):
                    week[team] = dict(g, team_side=team, picked=(team == p.side),
                                      opponent=p.game.away if team == p.game.home else p.game.home)
    rows = []
    for team in sorted(set(records) | set(week)):
        t = week.get(team)
        last = games.get(team, [])[-1] if games.get(team) else None
        upcoming = t is not None and t["state"] != "post"
        if upcoming:
            edge, edge_from = t["edge"], "week"
        elif t is not None:  # already played this week: that game is their last
            edge, edge_from = t["edge"], "last"
        else:                # bye (or no line yet): most recent finished game
            edge, edge_from = (last["edge"], "last") if last else (None, None)
        ep = confidence.edge_points(edge, edges)
        rec = records.get(team)
        played = (rec["w"] + rec["l"] + rec["p"]) if rec else 0
        scored = played >= confidence.MIN_GAMES
        team_pts = rec["points"] if scored else None
        rows.append({
            "team": team, "record": rec, "team_pts": team_pts, "games": played,
            "edge": edge, "edge_from": edge_from, **ep,
            "confidence": round(team_pts + ep["edge_pts"], 1) if scored else None,
            "game": t,
            "last": {k: last[k] for k in ("away", "home", "date", "picked", "side", "teaser_line", "teaser_result")} if last else None,
        })
    # Scored teams first (highest first), then teams still short of 5 games.
    rows.sort(key=lambda r: (r["confidence"] is None, -(r["confidence"] or 0),
                             -(r["record"]["w"] if r["record"] else 0), r["team"]))
    return {"date": day, "week": odds.week_number(days[0]) or odds.week_number(day),
            "start": days[0], "end": days[-1], "teaser": snapshot.TEASER, "min_games": confidence.MIN_GAMES, "teams": rows,
            "edges": edges,
            "graded": sum(1 for h in hist if h["action"] == "SPREAD" and h.get("teaser_result"))}


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
