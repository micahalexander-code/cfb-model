"""Weekly runner: Sagarin ratings + DraftKings lines -> picks.

Usage:
  python3 run.py              # today's games
  python3 run.py 20261114     # a specific date (YYYYMMDD)

Prints picks grouped by action and appends every evaluated game to
picks/YYYYMMDD.csv (one snapshot per run, so line moves are preserved).
Run before kickoff: ESPN removes lines once a game starts.
"""
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

import names
import odds
import sagarin
from model import (ACTION_EXCLUDED, ACTION_ML, ACTION_SKIP, ACTION_SPREAD,
                   FLAG_DISAGREE, TEASER_POINTS, Game, Pick, evaluate)

HERE = os.path.dirname(os.path.abspath(__file__))

CSV_FIELDS = [
    "run_at", "sagarin_as_of", "game_id", "kickoff", "away", "home", "neutral",
    "away_rating", "home_rating", "hfa", "dk_home_spread", "dk_home_ml", "dk_away_ml",
    "projected_home", "edge", "action", "side", "line", "odds", "teaser_line", "flags",
]


@dataclass
class Slate:
    day: str
    ratings: sagarin.SagarinRatings
    picks: List[Tuple[odds.Line, Pick]] = field(default_factory=list)
    no_line: List[odds.Line] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)


def build(day: str, teaser_points: float = TEASER_POINTS,
          ratings: Optional[sagarin.SagarinRatings] = None) -> Slate:
    ratings = ratings or sagarin.load()
    slate = Slate(day=day, ratings=ratings)
    for l in odds.load(day):
        h = names.to_sagarin(l.home, ratings.teams)
        a = names.to_sagarin(l.away, ratings.teams)
        if not h or not a:
            slate.unmatched += [n for n, m in ((l.home, h), (l.away, a)) if not m]
            continue
        if l.home_spread is None:
            slate.no_line.append(l)
            continue
        g = Game(home=h, away=a,
                 home_rating=ratings.teams[h].rating, away_rating=ratings.teams[a].rating,
                 dk_home_spread=l.home_spread, hfa=ratings.home_advantage,
                 neutral=l.neutral, dk_home_ml=l.home_ml, dk_away_ml=l.away_ml)
        slate.picks.append((l, evaluate(g, teaser_points)))
    slate.unmatched = sorted(set(slate.unmatched))
    return slate


def save_csv(slate: Slate) -> str:
    run_at = datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.join(HERE, "picks"), exist_ok=True)
    path = os.path.join(HERE, "picks", f"{slate.day}.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        for l, p in slate.picks:
            g = p.game
            w.writerow({
                "run_at": run_at, "sagarin_as_of": slate.ratings.as_of, "game_id": l.game_id,
                "kickoff": l.kickoff, "away": g.away, "home": g.home, "neutral": g.neutral,
                "away_rating": g.away_rating, "home_rating": g.home_rating, "hfa": g.hfa,
                "dk_home_spread": g.dk_home_spread, "dk_home_ml": g.dk_home_ml, "dk_away_ml": g.dk_away_ml,
                "projected_home": p.projected, "edge": p.edge, "action": p.action,
                "side": p.side, "line": p.line, "odds": p.odds, "teaser_line": p.teaser_line,
                "flags": "|".join(p.flags),
            })
    return path


def main(day: str) -> None:
    slate = build(day)
    r, picks = slate.ratings, slate.picks
    print(f"Sagarin through {r.as_of} | HFA {r.home_advantage} | slate {day}")
    print(f"{len(picks)} games with DraftKings lines\n")

    def section(title, rows):
        if rows:
            print(f"== {title} ({len(rows)}) ==")
            for _, p in rows:
                print(p.describe())
            print()

    spread = sorted([x for x in picks if x[1].action == ACTION_SPREAD], key=lambda x: -abs(x[1].edge))
    section("SPREAD PICKS (largest edge first)", [x for x in spread if FLAG_DISAGREE not in x[1].flags])
    section("CAUTION: MODEL AND BOOK DISAGREE ON WINNER", [x for x in spread if FLAG_DISAGREE in x[1].flags])
    section("HOME MONEYLINE (HFA through zero)", [x for x in picks if x[1].action == ACTION_ML])
    section("SKIPPED: PRICED IN", [x for x in picks if x[1].action == ACTION_SKIP and x[1].flags])
    section("EXCLUDED: NEUTRAL SITE", [x for x in picks if x[1].action == ACTION_EXCLUDED])
    if slate.no_line:
        print(f"No DraftKings line ({len(slate.no_line)}): " + "; ".join(
            f"{l.away} @ {l.home} ({l.status.replace('STATUS_', '').lower()})" for l in slate.no_line) + "\n")
    if slate.unmatched:
        print("UNMATCHED TEAM NAMES (add to names.py): " + ", ".join(slate.unmatched) + "\n")
    print(f"Saved {len(picks)} rows to {save_csv(slate)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y%m%d"))
