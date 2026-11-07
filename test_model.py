"""Run: python3 test_model.py"""
from model import (ACTION_EXCLUDED, ACTION_ML, ACTION_SKIP, ACTION_SPREAD,
                   FLAG_DISAGREE, FLAG_PRICED_IN, Game, evaluate)

HFA = 2.74


def g(home, away, hr, ar, spread, **kw):
    return Game(home=home, away=away, home_rating=hr, away_rating=ar,
                dk_home_spread=spread, hfa=HFA, **kw)


def test_byu_at_utah():
    p = evaluate(g("Utah", "BYU", 87.19, 82.49, -4))
    assert p.projected == 7.44 and p.edge == 3.44
    assert (p.action, p.side, p.line, p.teaser_line) == (ACTION_SPREAD, "Utah", -4, -1)
    assert not p.flags


def test_away_underdog_pick():
    # Home rated 85 vs 80 away -> proj home 7.74; book home -10 -> take away +10.
    p = evaluate(g("Home", "Away", 85, 80, -10))
    assert (p.side, p.line, p.teaser_line) == ("Away", 10, 13)


def test_hfa_through_zero_home_dog_takes_moneyline():
    p = evaluate(g("Home", "Away", 79, 80, 2.5, dk_home_ml=120))
    assert p.projected == 1.74
    assert (p.action, p.side, p.odds, p.teaser_line) == (ACTION_ML, "Home", 120, None)


def test_hfa_through_zero_book_favors_home_is_skipped():
    p = evaluate(g("Home", "Away", 79, 80, -1))
    assert p.action == ACTION_SKIP and FLAG_PRICED_IN in p.flags


def test_disagreement_is_flagged():
    # Home rated higher, model home +7.74, but DK makes home a 1-pt dog.
    p = evaluate(g("Home", "Away", 85, 80, 1))
    assert p.action == ACTION_SPREAD and p.side == "Home" and FLAG_DISAGREE in p.flags


def test_neutral_excluded():
    p = evaluate(g("Home", "Away", 85, 80, -3, neutral=True))
    assert p.action == ACTION_EXCLUDED


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print()
    print(evaluate(g("Utah", "BYU", 87.19, 82.49, -4)).describe())
