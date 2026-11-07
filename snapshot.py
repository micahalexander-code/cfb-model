"""Freeze finished games into history.json.

  python3 snapshot.py               # today and yesterday (US Eastern)
  python3 snapshot.py 20261107 ...  # specific dates

Each finished game with a DraftKings line is recorded ONCE: the ratings,
closing line, pick and result as they stood when it was first seen final.
Recorded games are never rewritten, so later Sagarin updates can't change
them. Run on a schedule by .github/workflows/snapshot.yml.

Guard: if Sagarin's ratings already include a game's date (e.g. a game that
was missed until after Monday's update), it is NOT recorded, since the
ratings would already know the result.
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import run

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "history.json")
LOCKED = os.path.join(HERE, "locked.json")
TEASER = 3.0
# Save a game's line once kickoff is this close (or it has started), so the
# saved line is effectively the closing line without a commit on every move.
LOCK_WINDOW = timedelta(minutes=90)
KEEP_LOCKED = timedelta(days=10)

MONTHS = {m: i for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
     "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], start=1)}


def eastern_today() -> date:
    # US Eastern without tz database: UTC-5 (close enough for picking dates;
    # we always also check yesterday).
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def ratings_through(as_of: str, game_date: date) -> Optional[date]:
    """'OCTOBER 31 SATURDAY - WEEK 9' -> the latest Oct 31 not far past game_date."""
    m = re.match(r"\s*([A-Z]+)\s+(\d+)", as_of.upper())
    if not m or m.group(1) not in MONTHS:
        return None
    d = date(game_date.year, MONTHS[m.group(1)], int(m.group(2)))
    # Bowl season spans New Year: December ratings vs a January game.
    if d > game_date + timedelta(days=180):
        d = d.replace(year=d.year - 1)
    return d


def default_days() -> List[str]:
    t = eastern_today()
    return [(t - timedelta(days=1)).strftime("%Y%m%d"), t.strftime("%Y%m%d")]


def load_history() -> List[dict]:
    if not os.path.exists(HISTORY):
        return []
    with open(HISTORY) as f:
        return json.load(f)


def _kickoff(iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def lock_lines(slate, locked: dict, now: datetime) -> bool:
    """Save the DK line for games about to start or in progress. True if changed."""
    changed = False
    for l, p in slate.picks:
        if l.completed or l.line_source == "saved":
            continue
        k = _kickoff(l.kickoff)
        if l.state == "pre" and (not k or k - now > LOCK_WINDOW):
            continue
        entry = {"date": slate.day, "kickoff": l.kickoff, "away": p.game.away, "home": p.game.home,
                 "dk_home_spread": l.home_spread, "dk_home_ml": l.home_ml, "dk_away_ml": l.away_ml}
        if locked.get(l.game_id) != entry:
            locked[l.game_id] = entry
            changed = True
    return changed


def dates_ok(slate) -> bool:
    """False when Sagarin's ratings already include this slate's results."""
    game_date = datetime.strptime(slate.day, "%Y%m%d").date()
    through = ratings_through(slate.ratings.as_of, game_date)
    return not (through and through >= game_date)


def finished_rows(slate, recorded_at: str) -> List[dict]:
    """History records for every finished game on the slate that has a line."""
    rows = []
    for l, p in slate.picks:
        if not l.completed:
            continue
        g = p.game
        rows.append({
            "game_id": l.game_id, "date": slate.day, "kickoff": l.kickoff, "recorded_at": recorded_at,
            "sagarin_as_of": slate.ratings.as_of, "hfa": g.hfa,
            "away": g.away, "home": g.home, "neutral": g.neutral,
            "away_rating": g.away_rating, "home_rating": g.home_rating,
            "dk_home_spread": g.dk_home_spread, "dk_home_ml": g.dk_home_ml,
            "dk_away_ml": g.dk_away_ml, "line_source": l.line_source,
            "projected": p.projected, "edge": p.edge, "action": p.action,
            "side": p.side, "line": p.line, "odds": p.odds,
            "teaser_points": TEASER, "teaser_line": p.teaser_line, "flags": p.flags,
            "away_score": l.away_score, "home_score": l.home_score,
            "result": p.result, "teaser_result": p.teaser_result,
        })
    return rows


def snapshot(days: List[str]) -> int:
    history = load_history()
    locked = {}
    if os.path.exists(LOCKED):
        with open(LOCKED) as f:
            locked = json.load(f)
    now_dt = datetime.now(timezone.utc)
    locked_changed = False
    seen = {h["game_id"] for h in history}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    ratings = None
    for day in days:
        slate = run.build(day, TEASER, ratings)
        ratings = slate.ratings
        locked_changed |= lock_lines(slate, locked, now_dt)
        if not dates_ok(slate):
            print(f"{day}: skipped, Sagarin ratings ({ratings.as_of}) already include this date")
            continue
        for row in finished_rows(slate, now):
            if row["game_id"] not in seen:
                history.append(row)
                seen.add(row["game_id"])
                added += 1
    if added:
        history.sort(key=lambda h: (h["date"], h["kickoff"], h["game_id"]))
        with open(HISTORY, "w") as f:
            json.dump(history, f, indent=1)
            f.write("\n")
    # Forget saved lines for old games (finished ones live in history.json).
    for gid in [g for g, e in locked.items()
                if (_kickoff(e.get("kickoff", "")) or now_dt) < now_dt - KEEP_LOCKED]:
        del locked[gid]
        locked_changed = True
    if locked_changed or not os.path.exists(LOCKED):
        with open(LOCKED, "w") as f:
            json.dump(locked, f, indent=1, sort_keys=True)
            f.write("\n")
    print(f"Added {added} finished game(s); history now has {len(history)}; "
          f"{len(locked)} saved line(s)")
    return added


if __name__ == "__main__":
    snapshot(sys.argv[1:] or default_days())
