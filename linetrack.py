"""Line Tracking: how each game's DraftKings spread and our edge move during the week.

Stored in lines.json, one entry per game:

  points  three checks a day (9 am, 3 pm and 10 pm Eastern), each taken by the
          first scheduled snapshot run after that time: DK spread, model line
          and edge as they stood then. Edge moves when the line moves or
          Sagarin updates.
  close   the line and edge when the game kicked off.

Lines for the next week usually post Sunday, right after Saturday's games, so
the earliest point is Sunday and a game gets three points a day through
kickoff. (ESPN's "open" line is NOT used: it can be a look-ahead line posted
weeks earlier, not the Sunday number.)
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional


HERE = os.path.dirname(os.path.abspath(__file__))
LINES = os.path.join(HERE, "lines.json")
CHECKS = [9, 15, 22]             # Eastern hours of the daily line checks: 9 am, 3 pm, 10 pm
KEEP = timedelta(days=21)        # forget games this long after kickoff

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:  # no tz database: fixed UTC-5
    EASTERN = timezone(timedelta(hours=-5))


def eastern(now: datetime) -> datetime:
    return now.astimezone(EASTERN)


def check_of(now: datetime) -> str:
    """The latest daily check time at or before `now`, as 'YYYYMMDD-HH' (Eastern).
    Before 9 am that is the previous day's 10 pm check."""
    e = eastern(now)
    past = [h for h in CHECKS if h <= e.hour]
    if past:
        return f"{e.date():%Y%m%d}-{past[-1]:02d}"
    return f"{e.date() - timedelta(days=1):%Y%m%d}-{CHECKS[-1]:02d}"


def week_window(day: str) -> List[str]:
    """Tuesday..Saturday of the CFB week containing `day` (Sun/Mon belong to the
    week that just ended)."""
    d = datetime.strptime(day, "%Y%m%d").date()
    tue = d - timedelta(days=(d.weekday() - 1) % 7)
    return [(tue + timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]


def upcoming_week(day: str) -> List[str]:
    """Tue..Sat of the week whose games are next: on Sunday/Monday that's the
    coming week, otherwise the week containing `day`."""
    d = datetime.strptime(day, "%Y%m%d").date()
    if d.weekday() in (6, 0):  # Sunday, Monday
        d += timedelta(days=(1 - d.weekday()) % 7)
    return week_window(d.strftime("%Y%m%d"))


def load() -> dict:
    try:
        with open(LINES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    with open(LINES, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")


def _point(p) -> dict:
    """DK spread, model line and edge for a pick."""
    return {"dk_home_spread": p.game.dk_home_spread, "projected": p.projected,
            "edge": p.edge, "action": p.action, "side": p.side}


def _kick(iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def track(data: dict, slates, now: datetime, check: Optional[str]) -> bool:
    """Update `data` from slates. Adds the point for `check` (see check_of) to
    games not started, if they don't have it yet, and the closing point for
    games that have kicked off.
    Returns True if anything changed."""
    changed = False
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    for s in slates:
        for l, p in s.picks:
            g = p.game
            e = data.get(l.game_id)
            if e is None:
                if l.state != "pre":
                    continue  # first seen after kickoff: nothing to track
                e = data[l.game_id] = {
                    "date": s.day, "kickoff": l.kickoff, "away": g.away, "home": g.home,
                    "neutral": g.neutral, "points": [], "close": None}
                changed = True
            if e["kickoff"] != l.kickoff:
                e["kickoff"], changed = l.kickoff, True
            if l.state == "pre":
                if check and not any(x.get("check") == check for x in e["points"]):
                    e["points"].append(dict(_point(p), check=check, at=stamp,
                                            sagarin_as_of=s.ratings.as_of, hfa=g.hfa))
                    changed = True
            elif e["close"] is None:
                e["close"] = dict(_point(p), at=stamp, sagarin_as_of=s.ratings.as_of, hfa=g.hfa)
                changed = True
    for gid in [k for k, e in data.items() if (_kick(e.get("kickoff")) or now) < now - KEEP]:
        del data[gid]
        changed = True
    return changed
