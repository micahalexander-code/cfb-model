"""Finished games for the team half of Confidence that History doesn't have.

History only holds games we could score with Sagarin ratings, so it is missing
the weeks we have no saved ratings for (Weeks 0-1, Week 7). The team half of
Confidence only needs the DraftKings line and the final score, so this fills
those dates from ESPN into archive/extra_games.json:

  python3 team_lines.py            # fill every season date History doesn't cover
  python3 team_lines.py --dry-run

Each row: game_id, date, kickoff, home, away (Sagarin names), neutral,
dk_home_spread, home_score, away_score. Never overwrites a stored row.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

import names
import odds
import sagarin

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRA = os.path.join(HERE, "archive", "extra_games.json")
SEASON_START = date(2026, 8, 22)  # Week 0 is Aug 29; a week early to be safe


def load() -> list:
    try:
        with open(EXTRA) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(extra: list) -> None:
    extra.sort(key=lambda r: (r["date"], r["kickoff"], r["game_id"]))
    with open(EXTRA, "w") as f:
        json.dump(extra, f, indent=1)
        f.write("\n")


def add_slate(slate, history_ids) -> int:
    """Store a slate's finished games that History skipped (used by snapshot.py
    when Sagarin's ratings already include the date). Returns rows added."""
    extra = load()
    seen = set(history_ids) | {e["game_id"] for e in extra}
    new = []
    for l, p in slate.picks:
        if l.completed and l.home_score is not None and l.game_id not in seen:
            new.append({"game_id": l.game_id, "date": slate.day, "kickoff": l.kickoff,
                        "home": p.game.home, "away": p.game.away, "neutral": l.neutral,
                        "dk_home_spread": l.home_spread, "dk_total": l.total,
                        "home_score": l.home_score, "away_score": l.away_score})
    if new:
        save(extra + new)
    return len(new)


def rows_for(day: str, ratings) -> list:
    rows = []
    for l in odds.load(day):
        if not l.completed or l.home_spread is None or l.home_score is None:
            continue
        h = names.to_sagarin(l.home, ratings.teams)
        a = names.to_sagarin(l.away, ratings.teams)
        if not h or not a:
            continue
        rows.append({"game_id": l.game_id, "date": day, "kickoff": l.kickoff, "home": h, "away": a,
                     "neutral": l.neutral, "dk_home_spread": l.home_spread, "dk_total": l.total,
                     "home_score": l.home_score, "away_score": l.away_score})
    return rows


def fill(dry_run: bool = False) -> int:
    import snapshot
    ratings = sagarin.load()
    history = snapshot.load_history()
    covered = {h["date"] for h in history}
    extra = load()
    seen = {h["game_id"] for h in history} | {e["game_id"] for e in extra}
    today = snapshot.eastern_today()
    added = 0
    d = SEASON_START
    while d < today:
        day = d.strftime("%Y%m%d")
        if day not in covered:
            new = [r for r in rows_for(day, ratings) if r["game_id"] not in seen]
            seen.update(r["game_id"] for r in new)
            extra += new
            added += len(new)
            if new:
                print(f"{day}: +{len(new)}")
        d += timedelta(days=1)
    if added and not dry_run:
        save(extra)
    print(f"{'Would add' if dry_run else 'Added'} {added}; extra games now {len(extra)}")
    return added


if __name__ == "__main__":
    fill("--dry-run" in sys.argv)
