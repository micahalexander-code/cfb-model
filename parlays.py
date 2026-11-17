"""Parlays: pair our high-confidence teaser picks into 2-leg parlays.

Legs: The Core Model teaser picks only (SPREAD, no DISAGREE), the picked team
teased 3 points, where the picked team's confidence score going into that week
is at least `min_conf`.

Confidence is taken as of the start of the week (Tuesday), using only what
was known before it: the team's
record against its own teased line in games before that date, and the edge
table from Core Model picks before that date (see confidence.py). So past
parlays are a fair backtest, not graded with hindsight.

Pairing (per CFB week, Tue-Sat): kickoff times play no part. Legs are
ranked by confidence; the top two form a parlay, then the next two, and so
on. An odd leg left over isn't used. (Earlier versions paired legs kicking
off within an hour of each other; dropped Nov 2026.)

Result: any leg lost -> LOSS; all legs pushed -> PUSH; otherwise WIN (a
pushed leg drops out, as at most books). Unfinished legs -> pending.
"""
from typing import Dict, List, Optional

import confidence
import linetrack

MAX_LEGS = 2  # was 3; 2-leg parlays backtested better (Nov 2026)


class AsOf:
    """Confidence scores as they stood going into a given date."""

    def __init__(self, history: List[dict], extra: List[dict]):
        self.history = history
        self.games = confidence.team_games(history + extra)
        self._edges: Dict[str, List[dict]] = {}

    def edges(self, day: str) -> List[dict]:
        if day not in self._edges:
            self._edges[day] = confidence.edge_table([h for h in self.history if h["date"] < day])
        return self._edges[day]

    def score(self, team: str, day: str, edge: float) -> dict:
        rows = [r for r in self.games.get(team, []) if r["date"] < day]
        rec = confidence._record(rows)
        played = rec["w"] + rec["l"] + rec["p"]
        ep = confidence.edge_points(edge, self.edges(day))
        conf = round(rec["points"] + ep["edge_pts"], 1) if played >= confidence.MIN_GAMES else None
        return {"confidence": conf, "record": rec, "games": played, **ep, "team_pts": rec["points"]}


def leg_result(legs: List[dict]) -> Optional[str]:
    res = [l["result"] for l in legs]
    if "LOSS" in res:
        return "LOSS"
    if any(r is None for r in res):
        return None  # still to play / in play
    return "PUSH" if all(r == "PUSH" for r in res) else "WIN"


def pair_by_confidence(legs: List[dict]) -> List[List[dict]]:
    """Highest confidence first, MAX_LEGS legs per parlay; leftovers unused."""
    legs = sorted(legs, key=lambda l: (-l["confidence"], l["kickoff"], l["team"]))
    out = [legs[i:i + MAX_LEGS] for i in range(0, len(legs) - MAX_LEGS + 1, MAX_LEGS)]
    return [sorted(g, key=lambda l: l["kickoff"]) for g in out]


def build(candidates: List[dict], asof: AsOf, min_conf: float) -> List[dict]:
    """candidates: one dict per Core Model teaser pick with game_id, date,
    kickoff, team (our side), opponent, home, teased_line, edge, result
    (WIN/LOSS/PUSH or None), state, scores. Returns parlays, newest first."""
    by_week: Dict[str, List[dict]] = {}
    for c in candidates:
        week = linetrack.week_window(c["date"])[0]
        # Scored as of the week's start (Tuesday), when a week-long parlay would be placed.
        s = asof.score(c["team"], week, c["edge"])
        if s["confidence"] is None or s["confidence"] < min_conf:
            continue
        by_week.setdefault(week, []).append(dict(c, **s))
    parlays = []
    for week, legs in by_week.items():
        for g in pair_by_confidence(legs):
            parlays.append({"week_start": week, "date": g[0]["date"], "kickoff": g[0]["kickoff"],
                            "legs": g, "result": leg_result(g)})
    parlays.sort(key=lambda p: p["kickoff"], reverse=True)
    return parlays
