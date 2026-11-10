"""Replay past weeks into history.json using saved Sagarin ratings.

  python3 backfill.py              # every week we have ratings for
  python3 backfill.py --dry-run    # show what would be added, write nothing

Ratings: archive/sagarin_ratings.json, taken from PDFs of Sagarin's page
(Wayback Machine): cfb_week1.pdf ... one per week. Ratings "through" a date
are used for games in the 7 days AFTER that date (the following week's
slate), never for games they already include. A week with no saved ratings
(Week 6) means the next week's games are skipped rather than scored with
stale ratings. Games before the first saved ratings are skipped too.

Lines: DraftKings closing lines from ESPN's game summaries (same feed the
live model uses for finished games).

Games already in history.json are never touched; new rows are marked
"backfill": true.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

import run
import sagarin
import snapshot

HERE = os.path.dirname(os.path.abspath(__file__))
RATINGS = os.path.join(HERE, "archive", "sagarin_ratings.json")
VALID_DAYS = 7  # ratings cover the week after their "through" date


def saved_ratings() -> List[Tuple[date, str, sagarin.SagarinRatings]]:
    with open(RATINGS) as f:
        weeks = json.load(f)
    out = []
    for w in weeks:
        teams = {t["team"]: sagarin.TeamRating(team=t["team"], division=t["division"],
                                               rating=t["rating"], rank=t["rank"],
                                               wins=t["wins"], losses=t["losses"])
                 for t in w["teams"]}
        out.append((datetime.strptime(w["through"], "%Y%m%d").date(), w["source"],
                    sagarin.SagarinRatings(as_of=w["as_of"], home_advantage=w["home_advantage"],
                                           teams=teams)))
    return sorted(out, key=lambda x: x[0])


def ratings_for(day: date, weeks) -> Optional[Tuple[str, sagarin.SagarinRatings]]:
    """The latest ratings through a date before `day`, if `day` is within the
    week after it."""
    best = None
    for through, source, r in weeks:
        if through < day <= through + timedelta(days=VALID_DAYS):
            if best is None or through > best[0]:
                best = (through, source, r)
    return (best[1], best[2]) if best else None


def backfill(dry_run: bool = False) -> int:
    weeks = saved_ratings()
    history = snapshot.load_history()
    live_days = {h["date"] for h in history if not h.get("backfill")}
    seen = {h["game_id"] for h in history}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    start = weeks[0][0] + timedelta(days=1)
    end = weeks[-1][0] + timedelta(days=VALID_DAYS)
    added = 0
    for d in [start + timedelta(days=i) for i in range((end - start).days + 1)]:
        day = d.strftime("%Y%m%d")
        if day in live_days:
            continue  # recorded live already
        pick = ratings_for(d, weeks)
        if pick is None:
            print(f"{day}: no saved ratings for this week, skipped")
            continue
        source, r = pick
        slate = run.build(day, snapshot.TEASER, r)
        if not slate.picks:
            continue
        if not snapshot.dates_ok(slate):  # never use ratings that already include the result
            print(f"{day}: ratings {r.as_of} include this date, skipped")
            continue
        rows = [dict(x, backfill=True) for x in snapshot.finished_rows(slate, now)
                if x["game_id"] not in seen]
        seen.update(x["game_id"] for x in rows)
        history += rows
        added += len(rows)
        acts = {}
        for x in rows:
            acts[x["action"]] = acts.get(x["action"], 0) + 1
        unfinished = sum(1 for l, _ in slate.picks if not l.completed)
        print(f"{day}: +{len(rows)} games from {source} (through {r.as_of}, HFA {r.home_advantage}) {acts}"
              + (f" | {unfinished} not final" if unfinished else "")
              + (f" | unmatched {slate.unmatched}" if slate.unmatched else "")
              + (f" | {len(slate.no_line)} without a line" if slate.no_line else ""))
    if added and not dry_run:
        history.sort(key=lambda h: (h["date"], h["kickoff"], h["game_id"]))
        with open(snapshot.HISTORY, "w") as f:
            json.dump(history, f, indent=1)
            f.write("\n")
    print(f"{'Would add' if dry_run else 'Added'} {added} game(s); history "
          f"{'would have' if dry_run else 'now has'} {len(history)}")
    return added


if __name__ == "__main__":
    backfill("--dry-run" in sys.argv)
