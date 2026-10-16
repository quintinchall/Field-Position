"""Run: python tests/test_all.py   (no pytest needed)"""
import os, sys, json, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import model, build_picks as bp

def close(a, b, tol=1e-3): return abs(a - b) <= tol
T = []
def test(f): T.append(f); return f

@test
def moneyline_conversion():
    assert close(model.ml_to_raw(-110), 0.5238)
    assert close(model.ml_to_raw(+200), 0.3333)
    p = model.ml_pair_to_prob(-110, -110); assert close(p, 0.5)
    p = model.ml_pair_to_prob(355, -460)  # LAC @ KC
    assert 0.77 < p < 0.80, p

@test
def spread_conversion():
    assert close(model.spread_to_prob(0), 0.5)
    assert close(model.spread_to_prob(-7) + model.spread_to_prob(7), 1.0)
    assert 0.68 < model.spread_to_prob(-7) < 0.72  # home -7 wins ~70%
    assert model.spread_to_prob(3) < 0.5           # home +3 is the underdog

@test
def market_prob_falls_back_to_spread():
    g = {"home": "KC", "away": "LAC", "ml_home": None, "ml_away": None, "spread_home": -8.5}
    assert close(model.market_prob(g), model.spread_to_prob(-8.5))
    assert model.market_prob({"home": "A", "away": "B"}) is None

@test
def elo_is_zero_sum_and_rewards_winner():
    h, a = model.elo_update(1500, 1500, 10)
    assert close(h + a, 3000) and h > 1500 > a
    h2, a2 = model.elo_update(1500, 1500, -10)
    assert h2 < 1500 < a2
    # blowout moves more than a squeaker
    assert model.elo_update(1500, 1500, 30)[0] > model.elo_update(1500, 1500, 3)[0]
    # neutral site removes home edge
    assert close(model.elo_expect(1500, 1500, neutral=True), 0.5)
    assert model.elo_expect(1500, 1500) > 0.5

@test
def elo_season_regression():
    games = [{"season": 2024, "home": "A", "away": "B", "home_score": 40, "away_score": 0},
             {"season": 2025, "home": "C", "away": "D", "home_score": 20, "away_score": 20}]
    r = model.run_elo(games)
    one = model.run_elo(games[:1])
    assert close(r["A"] - 1500, (one["A"] - 1500) * (2 / 3))

@test
def yard_line_metaphor():
    assert model.yard_line(0.5) == 50 and model.yard_line(0.9) == 10 and model.yard_line(0.999) == 1

@test
def notes_pool_gap_points_to_value_side():
    g = {"home": "KC", "away": "LAC", "spread_home": -10.5, "pool_spread_home": -7.5}
    kinds = dict(model.build_notes(g, 0.82))
    assert "edge" in kinds and "KC is the value side" in kinds["edge"]
    g = {"home": "KC", "away": "LAC", "spread_home": -6, "pool_spread_home": -8.5}
    assert "LAC is the value side" in dict(model.build_notes(g, 0.7))["edge"]

@test
def notes_flip_and_move():
    g = {"home": "GB", "away": "DAL", "spread_home": -1.5, "open_spread_home": 1}
    assert "flipped" in dict(model.build_notes(g, 0.52))["move"]
    g = {"home": "KC", "away": "LAC", "spread_home": -8.5, "open_spread_home": -7.5}
    t = dict(model.build_notes(g, 0.79))["move"]
    assert "1 pt toward KC" in t, t

@test
def notes_fade_only_when_crowd_overloaded():
    g = {"home": "PHI", "away": "CAR"}
    assert "fade" in dict(model.build_notes(g, 0.62, pick_pct={"PHI": 0.9}))
    assert "fade" not in dict(model.build_notes(g, 0.62, pick_pct={"PHI": 0.6}))
    assert "fade" not in dict(model.build_notes(g, 0.80, pick_pct={"PHI": 0.95}))

@test
def live_build_with_fake_nflverse():
    """Checks the nflverse sign convention (positive spread_line = home favored), records, week detection."""
    rows = [
        dict(game_id="2026_01_B_A", season="2026", game_type="REG", week="1", gameday="2026-09-10", gametime="20:20",
             away_team="B", home_team="A", away_score="10", home_score="24", location="Home",
             spread_line="3.0", home_moneyline="-160", away_moneyline="135"),
        dict(game_id="2026_02_A_JAC", season="2026", game_type="REG", week="2", gameday="2026-09-17", gametime="13:00",
             away_team="A", home_team="JAC", away_score="", home_score="", location="Home",
             spread_line="7.0", home_moneyline="", away_moneyline=""),
    ]
    tmp = tempfile.mkdtemp()
    bp.nflverse_games = lambda: rows
    bp.injury_notes = lambda s, w: {}
    bp.odds_api_lines = lambda: {}
    bp.SNAP_DIR = tmp
    d = bp.build_live()
    assert d["week"] == 2 and len(d["games"]) == 1
    g = d["games"][0]
    assert g["home"] == "JAX", "alias JAC->JAX"
    assert g["spread_home"] == -7.0 and g["pick"] == "JAX", "positive spread_line must mean home favored"
    assert g["away_record"] == "1-0" and g["home_record"] == "0-0"
    assert g["open_spread_home"] == -7.0  # first snapshot becomes the open
    assert os.path.exists(os.path.join(tmp, "2026-w02.json"))

@test
def manual_build_matches_schema():
    d = bp.build_manual(os.path.join(ROOT, "config", "manual_week.csv"))
    assert d["week"] == 6 and len(d["games"]) == 14
    for g in d["games"]:
        for k in ("id", "away", "home", "p_home", "pick", "pick_prob", "yard", "notes", "status"):
            assert k in g, (g["id"], k)
        assert 0.5 <= g["pick_prob"] <= 1 and g["pick"] in (g["home"], g["away"])
    json.dumps(d)

fails = 0
for f in T:
    try: f(); print("pass ", f.__name__)
    except AssertionError as e: fails += 1; print("FAIL ", f.__name__, e)
sys.exit(1 if fails else print(f"\n{len(T)} tests passed"))
