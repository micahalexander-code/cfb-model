"""Sagarin-vs-DraftKings spread model.

Rules (all margins from the HOME team's perspective; positive = home wins by):

  projected = A(home) - A(away) + HFA
  book      = -(DraftKings home spread)          e.g. Utah -4  -> +4
  edge      = projected - book

  1. Neutral-site games: excluded from the model.
  2. HFA pushes the projection "through 0" (home team rated LOWER, but
     projected > 0):
       - DraftKings has home as underdog  -> bet HOME MONEYLINE (no teaser).
       - DraftKings has home favored/pick -> SKIP. Something is priced in
         that the model doesn't see. Logged as PRICED_IN.
  3. Model and book disagree on who wins (otherwise) -> spread pick by edge,
     flagged DISAGREE (higher risk / volatility).
  4. Otherwise: edge > 0 -> home side at the DK spread; edge < 0 -> away side.
     Teaser: tease that pick TEASER_POINTS in the bettor's favor.
"""
from dataclasses import dataclass, field
from typing import List, Optional

TEASER_POINTS = 3.0
# Minimum projected home margin for the home-moneyline rule. 0 = any
# projection above zero triggers it (expected to be rare).
MIN_ML_MARGIN = 0.0

ACTION_SPREAD = "SPREAD"
ACTION_ML = "MONEYLINE"
ACTION_SKIP = "SKIP"
ACTION_EXCLUDED = "EXCLUDED"

FLAG_PRICED_IN = "PRICED_IN"   # book favors the lower-rated home team; stay away
FLAG_DISAGREE = "DISAGREE"     # model and book pick different winners; high risk
FLAG_NEUTRAL = "NEUTRAL_SITE"


@dataclass
class Game:
    home: str
    away: str
    home_rating: float
    away_rating: float
    dk_home_spread: float          # DraftKings line for the home team (Utah -4 -> -4.0)
    hfa: float
    neutral: bool = False
    dk_home_ml: Optional[int] = None   # American odds, e.g. +135
    dk_away_ml: Optional[int] = None


@dataclass
class Pick:
    game: Game
    projected: float               # home margin per model
    book: float                    # home margin per DraftKings
    edge: float
    action: str
    side: Optional[str] = None     # team name being bet
    line: Optional[float] = None   # spread for that side, or None for ML
    odds: Optional[int] = None     # moneyline odds when action is ML
    teaser_line: Optional[float] = None
    flags: List[str] = field(default_factory=list)
    result: Optional[str] = None          # WIN / LOSS / PUSH once the game is final
    teaser_result: Optional[str] = None   # per-leg teaser result (spread picks only)

    def describe(self) -> str:
        g = self.game
        head = f"{g.away} @ {g.home}"
        nums = f"proj home {self.projected:+.2f} | DK home {g.dk_home_spread:+g} | edge {self.edge:+.2f}"
        if self.action == ACTION_SPREAD:
            bet = f"{self.side} {self.line:+g}  (teaser: {self.side} {self.teaser_line:+g})"
        elif self.action == ACTION_ML:
            odds = f" {self.odds:+d}" if self.odds is not None else ""
            bet = f"{self.side} ML{odds}"
        else:
            bet = self.action
        flags = f"  [{', '.join(self.flags)}]" if self.flags else ""
        return f"{head}: {bet}{flags}\n    {nums}"


WIN, LOSS, PUSH = "WIN", "LOSS", "PUSH"


def _cover(margin_plus_line: float) -> str:
    return WIN if margin_plus_line > 0 else LOSS if margin_plus_line < 0 else PUSH


def grade(p: "Pick", home_score: int, away_score: int) -> "Pick":
    """Fill in result fields from a FINAL score. Skips/exclusions stay ungraded."""
    if p.side is None:
        return p
    side_margin = (home_score - away_score) * (1 if p.side == p.game.home else -1)
    if p.action == ACTION_SPREAD:
        p.result = _cover(side_margin + p.line)
        p.teaser_result = _cover(side_margin + p.teaser_line)
    elif p.action == ACTION_ML:
        p.result = _cover(side_margin)
    return p


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def evaluate(g: Game, teaser_points: float = TEASER_POINTS) -> Pick:
    projected = round(g.home_rating - g.away_rating + (0.0 if g.neutral else g.hfa), 2)
    book = -g.dk_home_spread
    edge = round(projected - book, 2)
    pick = Pick(game=g, projected=projected, book=book, edge=edge, action=ACTION_SKIP)

    if g.neutral:
        pick.action = ACTION_EXCLUDED
        pick.flags.append(FLAG_NEUTRAL)
        return pick

    # Rule 2: HFA pushes the projection through zero.
    if g.home_rating < g.away_rating and projected > MIN_ML_MARGIN:
        if book < 0:  # DK has home as the underdog
            pick.action = ACTION_ML
            pick.side = g.home
            pick.odds = g.dk_home_ml
        else:
            pick.flags.append(FLAG_PRICED_IN)
        return pick

    if edge == 0:
        return pick

    # Rule 3: model and book pick different outright winners.
    if _sign(projected) != 0 and _sign(book) != 0 and _sign(projected) != _sign(book):
        pick.flags.append(FLAG_DISAGREE)

    # Rule 4: spread pick + teaser.
    pick.action = ACTION_SPREAD
    if edge > 0:
        pick.side, pick.line = g.home, g.dk_home_spread
    else:
        pick.side, pick.line = g.away, -g.dk_home_spread
    pick.teaser_line = pick.line + teaser_points
    return pick
