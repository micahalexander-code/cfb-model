"""Parse Jeff Sagarin's college football ratings page.

Pulls each team's overall RATING (the number after "A =" / "AA =") and the
listed HOME ADVANTAGE. Standard library only.
"""
import re
import sys
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional

SAGARIN_URL = "http://sagarin.com/sports/cfsend.htm"

# e.g. "  10  Utah                 A  =  87.19    8   0   69.50(  60) ..."
TEAM_ROW = re.compile(
    r"^\s*(?P<rank>\d+)\s+(?P<team>.+?)\s+(?P<div>A|AA)\s+=\s+(?P<rating>-?\d+\.\d+)\s+(?P<w>\d+)\s+(?P<l>\d+)"
)
HFA_ROW = re.compile(r"HOME ADVANTAGE=\[\s*(?P<hfa>-?\d+\.\d+)\s*\]")
AS_OF = re.compile(r"through (?:games|results) of ([^<\n]+)", re.IGNORECASE)


@dataclass
class TeamRating:
    team: str
    division: str  # "A" = FBS, "AA" = FCS
    rating: float
    rank: int
    wins: int
    losses: int


@dataclass
class SagarinRatings:
    as_of: str
    home_advantage: float
    teams: Dict[str, TeamRating]

    def get(self, name: str) -> Optional[TeamRating]:
        return self.teams.get(name)


def fetch_html(url: str = SAGARIN_URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("latin-1")


def parse(html: str) -> SagarinRatings:
    hfa_match = HFA_ROW.search(html)
    if not hfa_match:
        raise ValueError("Could not find HOME ADVANTAGE on Sagarin page")
    as_of_match = AS_OF.search(html)

    teams: Dict[str, TeamRating] = {}
    for line in html.splitlines():
        m = TEAM_ROW.match(line)
        if not m:
            continue
        name = m.group("team").strip()
        # The page lists teams again by conference further down; the first
        # (overall ranking) listing is authoritative.
        if name in teams:
            continue
        teams[name] = TeamRating(
            team=name,
            division=m.group("div"),
            rating=float(m.group("rating")),
            rank=int(m.group("rank")),
            wins=int(m.group("w")),
            losses=int(m.group("l")),
        )

    if not teams:
        raise ValueError("No team rows parsed from Sagarin page")
    return SagarinRatings(
        as_of=as_of_match.group(1).strip() if as_of_match else "unknown",
        home_advantage=float(hfa_match.group("hfa")),
        teams=teams,
    )


def load(path: Optional[str] = None) -> SagarinRatings:
    """Load from a saved HTML file if given, else fetch live."""
    if path:
        with open(path, encoding="latin-1") as f:
            return parse(f.read())
    return parse(fetch_html())


if __name__ == "__main__":
    r = load(sys.argv[1] if len(sys.argv) > 1 else None)
    fbs = sum(1 for t in r.teams.values() if t.division == "A")
    print(f"As of: {r.as_of}")
    print(f"Home advantage: {r.home_advantage}")
    print(f"Teams parsed: {len(r.teams)} ({fbs} FBS, {len(r.teams) - fbs} FCS)")
    for t in sorted(r.teams.values(), key=lambda t: t.rank)[:10]:
        print(f"  {t.rank:>3}  {t.team:<22} {t.division:<2} {t.rating:6.2f}")
