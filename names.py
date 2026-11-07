"""Map ESPN team names (location field) to Sagarin's spellings.

Names that already match exactly don't need an entry. When run.py reports an
unmatched team, add it here.
"""
from typing import Dict, Optional

ESPN_TO_SAGARIN: Dict[str, str] = {
    "App State": "Appalachian State",
    "Arkansas-Pine Bluff": "Ark.-Pine Bluff",
    "Army": "Army West Point",
    "Cal Poly": "Cal Poly-SLO",
    "Florida International": "Fla. International",
    "Grambling": "Grambling State",
    "Long Island University": "LIU Post",
    "Louisiana": "Louisiana-Lafayette",
    "McNeese": "McNeese State",
    "Miami": "Miami-Florida",
    "Miami (OH)": "Miami-Ohio",
    "Mississippi Valley State": "Miss. Valley State",
    "Monmouth": "Monmouth-NJ",
    "Nicholls": "Nicholls State",
    "North Carolina A&T": "NC A&T",
    "North Carolina Central": "NC Central",
    "Ole Miss": "Mississippi",
    "Sam Houston": "Sam Houston State",
    "San José State": "San Jose State",
    "Southeast Missouri State": "SE Missouri State",
    "Southern": "Southern U.",
    "Stonehill": "Stonehill College",
    "UAlbany": "Albany-NY",
    "UCF": "Central Florida(UCF)",
    "UConn": "Connecticut",
    "UL Monroe": "LouisianaMonroe(ULM)",
    "USC": "Southern California",
    "UT Martin": "Tennessee-Martin",
    "UT Rio Grande Valley": "UTRGV",
}


def to_sagarin(espn_name: str, sagarin_names) -> Optional[str]:
    if espn_name in sagarin_names:
        return espn_name
    alias = ESPN_TO_SAGARIN.get(espn_name)
    return alias if alias in sagarin_names else None
