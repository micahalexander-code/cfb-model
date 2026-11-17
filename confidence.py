"""Confidence score (0-100): how much we trust a pick to cover its TEASED line.

Two parts, 50 points each, both built from finished games (History):

  Team score  = 50 x the team's win rate against its OWN DraftKings closing
                spread teased 3 points, in EVERY finished game this season
                with a line (any pick type, neutral sites too; History plus
                archive/extra_games.json for weeks we have no ratings for).
                1-0 -> 50, 1-1 -> 25, 0-1 -> 0. Pushes don't count.
  Edge score  = 50 x the teaser win rate of the edge range of the team's
                game this week (or its most recent one if it already played
                or is on a bye) (same ranges as the History tab: 0-1, 1-2,
                2-3, 3+, Core Model picks only). 100% -> 50.

A team needs MIN_GAMES (5) graded games (pushes count) before it gets a score. An edge range with no graded games counts as a
neutral 25 - a coin flip - until data comes in.
"""
from typing import Dict, List, Optional

POINTS = 50.0
NEUTRAL = POINTS / 2
MIN_GAMES = 5  # a team needs this many games against our teased line to get a score
BUCKETS = [(0, 1), (1, 2), (2, 3), (3, None)]


def bucket_label(lo, hi) -> str:
    return f"{lo}+" if hi is None else f"{lo}–{hi}"


def bucket_of(edge: float):
    e = abs(edge)
    for lo, hi in BUCKETS:
        if e >= lo and (hi is None or e < hi):
            return lo, hi
    return BUCKETS[-1]


def _record(rows: List[dict]) -> dict:
    w = sum(r.get("teaser_result") == "WIN" for r in rows)
    l = sum(r.get("teaser_result") == "LOSS" for r in rows)
    p = sum(r.get("teaser_result") == "PUSH" for r in rows)
    rate = w / (w + l) if w + l else None
    return {"w": w, "l": l, "p": p, "rate": rate,
            "points": round(POINTS * rate, 1) if rate is not None else NEUTRAL}


def is_core(r: dict) -> bool:
    return r["action"] == "SPREAD" and "DISAGREE" not in (r.get("flags") or [])


def edge_table(history: List[dict]) -> List[dict]:
    core = [r for r in history if is_core(r) and r.get("teaser_result")]
    out = []
    for lo, hi in BUCKETS:
        rows = [r for r in core if bucket_of(r["edge"]) == (lo, hi)]
        out.append(dict(_record(rows), label=bucket_label(lo, hi), lo=lo, hi=hi))
    return out


TEASER = 3.0


def own_result(margin: int, line: float) -> str:
    """A team's result against its own spread teased TEASER points."""
    v = margin + line + TEASER
    return "WIN" if v > 0 else "LOSS" if v < 0 else "PUSH"


def team_games(games: List[dict]) -> Dict[str, List[dict]]:
    """Every finished game with a DK line, per team, graded against that
    team's own spread teased TEASER points. `games` rows need home, away,
    dk_home_spread, home_score, away_score (History rows or extra games)."""
    by_team: Dict[str, List[dict]] = {}
    seen = set()
    for r in games:
        if (r.get("home_score") is None or r.get("away_score") is None
                or r.get("dk_home_spread") is None or r["game_id"] in seen):
            continue
        seen.add(r["game_id"])
        m = r["home_score"] - r["away_score"]
        for team, opp, margin, line in ((r["home"], r["away"], m, r["dk_home_spread"]),
                                        (r["away"], r["home"], -m, -r["dk_home_spread"])):
            by_team.setdefault(team, []).append({
                "game_id": r["game_id"], "date": r["date"], "kickoff": r.get("kickoff") or "",
                "team": team, "opponent": opp, "home": team == r["home"],
                "line": line, "teased_line": line + TEASER, "margin": margin,
                "teaser_result": own_result(margin, line), "edge": r.get("edge")})
    for rows in by_team.values():
        rows.sort(key=lambda x: (x["kickoff"], x["game_id"]))
    return by_team


def team_table(history: List[dict]) -> Dict[str, dict]:
    return {t: _record(rows) for t, rows in sorted(team_games(history).items())}


def edge_points(edge: Optional[float], edges: List[dict]) -> dict:
    if edge is None:
        return {"edge_range": None, "edge_pts": NEUTRAL}
    lo, hi = bucket_of(edge)
    e = next(b for b in edges if (b["lo"], b["hi"]) == (lo, hi))
    return {"edge_range": e["label"], "edge_pts": e["points"]}
