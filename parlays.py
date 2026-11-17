"""Parlays: group our high-confidence teaser picks into 2-3 leg parlays.

Legs: The Core Model teaser picks only (SPREAD, no DISAGREE), the picked team
teased 3 points, where the picked team's confidence score going into that day
is at least `min_conf`.

Confidence "as of" a date uses only what was known before it: the team's
record against its own teased line in games before that date, and the edge
table from Core Model picks before that date (see confidence.py). So past
parlays are a fair backtest, not graded with hindsight.

Grouping (per day): legs sorted by kickoff; the earliest unused leg is
grouped with up to 2 more unused legs kicking off within WINDOW of it,
highest confidence first. A leg with no partner in its window isn't used.

Result: any leg lost -> LOSS; all legs pushed -> PUSH; otherwise WIN (a
pushed leg drops out, as at most books). Unfinished legs -> pending.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import confidence

WINDOW = timedelta(hours=1)
MAX_LEGS = 3


def _kick(iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


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


def group(legs: List[dict]) -> List[List[dict]]:
    """2-3 leg groups from one day's eligible legs (see module doc)."""
    legs = sorted(legs, key=lambda l: (l["kickoff"], -l["confidence"], l["team"]))
    used, out = set(), []
    for i, first in enumerate(legs):
        if first["game_id"] in used:
            continue
        k0 = _kick(first["kickoff"])
        mates = [l for l in legs[i + 1:] if l["game_id"] not in used
                 and k0 and _kick(l["kickoff"]) and _kick(l["kickoff"]) - k0 <= WINDOW]
        if not mates:
            continue
        mates.sort(key=lambda l: (-l["confidence"], l["kickoff"]))
        g = [first] + mates[:MAX_LEGS - 1]
        used.update(l["game_id"] for l in g)
        out.append(sorted(g, key=lambda l: l["kickoff"]))
    return out


def build(candidates: List[dict], asof: AsOf, min_conf: float) -> List[dict]:
    """candidates: one dict per Core Model teaser pick with game_id, date,
    kickoff, team (our side), opponent, home, teased_line, edge, result
    (WIN/LOSS/PUSH or None), state, scores. Returns parlays, newest first."""
    by_day: Dict[str, List[dict]] = {}
    for c in candidates:
        s = asof.score(c["team"], c["date"], c["edge"])
        if s["confidence"] is None or s["confidence"] < min_conf:
            continue
        by_day.setdefault(c["date"], []).append(dict(c, **s))
    parlays = []
    for day, legs in by_day.items():
        for g in group(legs):
            parlays.append({"date": day, "kickoff": g[0]["kickoff"], "legs": g, "result": leg_result(g),
                            "window_min": round((_kick(g[-1]["kickoff"]) - _kick(g[0]["kickoff"])).total_seconds() / 60)})
    parlays.sort(key=lambda p: p["kickoff"], reverse=True)
    return parlays
