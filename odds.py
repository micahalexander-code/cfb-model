"""DraftKings college football lines via ESPN's public scoreboard feed.

DraftKings blocks direct requests to its own API, but ESPN's scoreboard JSON
carries DraftKings lines (provider "Draft Kings") plus a neutral-site flag.
ESPN removes the line once a game kicks off, so run this before kickoff.
"""
import json
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/"
    "scoreboard?groups=80&limit=400&dates={dates}"
)
DK_PROVIDER = "draft kings"


@dataclass
class Line:
    game_id: str
    kickoff: str
    status: str
    home: str            # ESPN location name, e.g. "App State"
    away: str
    neutral: bool
    home_spread: Optional[float]   # DraftKings line for the home team (-6.5 = home favored)
    home_spread_odds: Optional[int]
    away_spread_odds: Optional[int]
    home_ml: Optional[int]
    away_ml: Optional[int]


def _int(v) -> Optional[int]:
    try:
        return int(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_json(dates: str) -> dict:
    req = urllib.request.Request(SCOREBOARD.format(dates=dates), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def parse(data: dict) -> List[Line]:
    lines = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c["team"] for c in comp["competitors"]}
        dk = next(
            (o for o in comp.get("odds") or []
             if o.get("provider", {}).get("name", "").lower() == DK_PROVIDER),
            None,
        )
        home_spread = hso = aso = hml = aml = None
        if dk:
            ps = dk.get("pointSpread", {})
            home_spread = _float(ps.get("home", {}).get("close", {}).get("line"))
            if home_spread is None:
                home_spread = _float(dk.get("spread"))
            hso = _int(ps.get("home", {}).get("close", {}).get("odds"))
            aso = _int(ps.get("away", {}).get("close", {}).get("odds"))
            ml = dk.get("moneyline", {})
            hml = _int(ml.get("home", {}).get("close", {}).get("odds"))
            aml = _int(ml.get("away", {}).get("close", {}).get("odds"))
        lines.append(Line(
            game_id=ev["id"],
            kickoff=ev.get("date", ""),
            status=ev["status"]["type"]["name"],
            home=teams["home"]["location"],
            away=teams["away"]["location"],
            neutral=bool(comp.get("neutralSite")),
            home_spread=home_spread,
            home_spread_odds=hso,
            away_spread_odds=aso,
            home_ml=hml,
            away_ml=aml,
        ))
    return lines


def load(dates: Optional[str] = None) -> List[Line]:
    """dates: YYYYMMDD or YYYYMMDD-YYYYMMDD. Defaults to today."""
    return parse(fetch_json(dates or date.today().strftime("%Y%m%d")))


if __name__ == "__main__":
    import sys
    for l in load(sys.argv[1] if len(sys.argv) > 1 else None):
        line = f"{l.home_spread:+g}" if l.home_spread is not None else "no line"
        print(f"{l.away:>22} @ {l.home:<22} {line:>8}  ML {l.home_ml}/{l.away_ml}  {'NEUTRAL ' if l.neutral else ''}{l.status}")
