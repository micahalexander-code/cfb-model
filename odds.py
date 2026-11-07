"""DraftKings college football lines and scores via ESPN's public feeds.

DraftKings blocks direct requests to its own API, but ESPN's scoreboard JSON
carries DraftKings lines (provider "Draft Kings"), a neutral-site flag and
scores. The scoreboard drops the line once a game kicks off; for those games
we read the DraftKings CLOSING line from ESPN's per-game summary feed.
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
SCOREBOARD = ESPN + "/scoreboard?groups=80&limit=400&dates={dates}"
SUMMARY = ESPN + "/summary?event={id}"
DK_PROVIDER = "draft kings"

LINE_CURRENT = "current"   # pregame line from the scoreboard
LINE_CLOSING = "closing"   # closing line from the game summary (live/final games)
LINE_SAVED = "saved"       # line our scheduled snapshot saved before ESPN dropped it

# Closing lines for finished games never change; keep them for the process lifetime.
_closing_cache: Dict[str, dict] = {}
# Last DraftKings odds seen for each game (pregame or closing). If ESPN later
# fails to return a line, the game keeps the one we already saw.
_seen: Dict[str, dict] = {}


@dataclass
class Line:
    game_id: str
    kickoff: str
    status: str
    state: str                       # "pre", "in" or "post"
    completed: bool
    home: str                        # ESPN location name, e.g. "App State"
    away: str
    neutral: bool
    home_score: Optional[int]
    away_score: Optional[int]
    home_spread: Optional[float]     # DraftKings line for the home team (-6.5 = home favored)
    home_spread_odds: Optional[int]
    away_spread_odds: Optional[int]
    home_ml: Optional[int]
    away_ml: Optional[int]
    line_source: Optional[str] = None


def _int(v) -> Optional[int]:
    try:
        return int(float(str(v).replace("+", "")))
    except (TypeError, ValueError):
        return None


def _float(v) -> Optional[float]:
    try:
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_json(dates: str) -> dict:
    return _get(SCOREBOARD.format(dates=dates))


def _dk(odds_list) -> Optional[dict]:
    return next((o for o in odds_list or []
                 if o.get("provider", {}).get("name", "").lower() == DK_PROVIDER), None)


def _apply_odds(l: Line, dk: dict, source: str) -> None:
    ps = dk.get("pointSpread", {})
    ml = dk.get("moneyline", {})
    l.home_spread = _float(ps.get("home", {}).get("close", {}).get("line"))
    if l.home_spread is None:
        l.home_spread = _float(dk.get("spread"))
    l.home_spread_odds = _int(ps.get("home", {}).get("close", {}).get("odds"))
    l.away_spread_odds = _int(ps.get("away", {}).get("close", {}).get("odds"))
    l.home_ml = _int(ml.get("home", {}).get("close", {}).get("odds"))
    l.away_ml = _int(ml.get("away", {}).get("close", {}).get("odds"))
    # Summary feed also carries flat moneyLine fields.
    if l.home_ml is None:
        l.home_ml = _int((dk.get("homeTeamOdds") or {}).get("moneyLine"))
    if l.away_ml is None:
        l.away_ml = _int((dk.get("awayTeamOdds") or {}).get("moneyLine"))
    if l.home_spread is not None:
        l.line_source = source


def _closing_odds(game_id: str, completed: bool) -> Optional[dict]:
    if game_id in _closing_cache:
        return _closing_cache[game_id]
    dk = None
    for attempt in range(3):  # ESPN occasionally errors or omits pickcenter; retry
        try:
            dk = _dk(_get(SUMMARY.format(id=game_id)).get("pickcenter"))
        except Exception:
            dk = None
        if dk:
            break
        time.sleep(0.5 * (attempt + 1))
    if dk and completed:
        _closing_cache[game_id] = dk
    return dk


def parse(data: dict) -> List[Line]:
    lines = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        by_side = {c["homeAway"]: c for c in comp["competitors"]}
        st = ev["status"]["type"]
        l = Line(
            game_id=ev["id"],
            kickoff=ev.get("date", ""),
            status=st["name"],
            state=st.get("state", ""),
            completed=bool(st.get("completed")),
            home=by_side["home"]["team"]["location"],
            away=by_side["away"]["team"]["location"],
            neutral=bool(comp.get("neutralSite")),
            home_score=_int(by_side["home"].get("score")) if st.get("state") != "pre" else None,
            away_score=_int(by_side["away"].get("score")) if st.get("state") != "pre" else None,
            home_spread=None, home_spread_odds=None, away_spread_odds=None,
            home_ml=None, away_ml=None,
        )
        dk = _dk(comp.get("odds"))
        if dk:
            _apply_odds(l, dk, LINE_CURRENT)
            if l.home_spread is not None:
                _seen[l.game_id] = dk
        lines.append(l)
    return lines


def fill_closing_lines(lines: List[Line]) -> None:
    """For games that have started (no scoreboard line), pull the closing line."""
    todo = [l for l in lines if l.home_spread is None and l.state in ("in", "post")]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda l: _closing_odds(l.game_id, l.completed), todo))
    for l, dk in zip(todo, results):
        if dk:
            _apply_odds(l, dk, LINE_CLOSING)
            if l.home_spread is not None:
                _seen[l.game_id] = dk
        if l.home_spread is None and l.game_id in _seen:
            # Summary feed failed this time; reuse the last line we saw.
            _apply_odds(l, _seen[l.game_id], LINE_CLOSING)


def load(dates: Optional[str] = None) -> List[Line]:
    """dates: YYYYMMDD. Defaults to today."""
    lines = parse(fetch_json(dates or date.today().strftime("%Y%m%d")))
    fill_closing_lines(lines)
    return lines


if __name__ == "__main__":
    import sys
    for l in load(sys.argv[1] if len(sys.argv) > 1 else None):
        line = f"{l.home_spread:+g} ({l.line_source})" if l.home_spread is not None else "no line"
        score = f"{l.away_score}-{l.home_score}" if l.home_score is not None else ""
        print(f"{l.away:>22} @ {l.home:<22} {line:>18}  ML {l.home_ml}/{l.away_ml}  {score:>7} {l.status}")
