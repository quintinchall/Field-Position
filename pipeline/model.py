"""Pure functions. No network, no files. Everything here is unit tested."""
import math

SPREAD_SD = 13.45  # std dev of NFL final margin around the closing spread


def ml_to_raw(ml):
    """American moneyline -> implied probability, vig still included."""
    ml = float(ml)
    if ml == 0:
        raise ValueError("moneyline cannot be 0")
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def ml_pair_to_prob(ml_away, ml_home):
    """Vig-free home win probability from both moneylines."""
    a, h = ml_to_raw(ml_away), ml_to_raw(ml_home)
    return h / (a + h)


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def spread_to_prob(spread_home):
    """spread_home uses sportsbook sign: -7 means home favored by 7."""
    return norm_cdf(-float(spread_home) / SPREAD_SD)


def market_prob(game):
    """Best available home win prob: moneylines first, spread as fallback."""
    if game.get("ml_home") not in (None, "") and game.get("ml_away") not in (None, ""):
        return ml_pair_to_prob(game["ml_away"], game["ml_home"])
    if game.get("spread_home") not in (None, ""):
        return spread_to_prob(game["spread_home"])
    return None


# ---------------- Elo ----------------
ELO_HFA = 40.0       # home field in Elo points (about 1.4 pts of spread; modern HFA is small)
ELO_K = 20.0
ELO_REVERT = 1 / 3   # regress a third toward 1500 each offseason


def elo_expect(r_home, r_away, neutral=False):
    diff = r_home - r_away + (0 if neutral else ELO_HFA)
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def elo_update(r_home, r_away, home_margin, neutral=False):
    """Returns new (r_home, r_away). Margin-of-victory multiplier per FiveThirtyEight's NFL Elo."""
    exp = elo_expect(r_home, r_away, neutral)
    actual = 1.0 if home_margin > 0 else 0.0 if home_margin < 0 else 0.5
    winner_diff = (r_home - r_away + (0 if neutral else ELO_HFA)) * (1 if home_margin >= 0 else -1)
    mov = math.log(abs(home_margin) + 1) * (2.2 / (winner_diff * 0.001 + 2.2)) if home_margin != 0 else 1.0
    delta = ELO_K * mov * (actual - exp)
    return r_home + delta, r_away - delta


def run_elo(games):
    """games: iterable of dicts with season, home, away, home_score, away_score, neutral (sorted by date).
    Returns ratings dict after all completed games."""
    ratings, season = {}, None
    for g in games:
        if g["season"] != season:
            if season is not None:
                ratings = {t: 1500 + (r - 1500) * (1 - ELO_REVERT) for t, r in ratings.items()}
            season = g["season"]
        h, a = g["home"], g["away"]
        rh, ra = ratings.get(h, 1500.0), ratings.get(a, 1500.0)
        ratings[h], ratings[a] = elo_update(rh, ra, g["home_score"] - g["away_score"], g.get("neutral", False))
    return ratings


# ---------------- Notes ----------------
def fmt_spread(team, spread):
    if spread == 0:
        return f"{team} pick'em"
    return f"{team} {spread:+g}"


def favored(game, p_home):
    return (game["home"], p_home) if p_home >= 0.5 else (game["away"], 1 - p_home)


def build_notes(game, p_home, alt_p_home=None, alt_label="Second model", pick_pct=None):
    """Plain-English reasons. Each note is (kind, text). Ordered by importance."""
    notes = []
    fav, pfav = favored(game, p_home)
    dog = game["away"] if fav == game["home"] else game["home"]
    cur, opn, pool = game.get("spread_home"), game.get("open_spread_home"), game.get("pool_spread_home")

    # 1. Pool line vs market: the most exploitable gap in spread pools
    if pool is not None and cur is not None and abs(pool - cur) >= 1.5:
        better = game["home"] if cur < pool else game["away"]
        notes.append(("edge", f"Your pool has {fmt_spread(game['home'], pool)}; the market is now "
                              f"{fmt_spread(game['home'], cur)}. Against the pool line, {better} is the value side."))

    # 2. Line movement since open
    if opn is not None and cur is not None:
        move = cur - opn
        if opn != 0 and cur != 0 and (opn > 0) != (cur > 0):
            now_fav = game["home"] if cur < 0 else game["away"]
            notes.append(("move", f"Line flipped: opened {fmt_spread(game['home'], opn)}, now "
                                  f"{fmt_spread(game['home'], cur)}. Money came in on {now_fav}."))
        elif abs(move) >= 1:
            toward = game["home"] if move < 0 else game["away"]
            notes.append(("move", f"Moved {abs(move):g} pt{'s' if abs(move) != 1 else ''} toward {toward} since open "
                                  f"({fmt_spread(game['home'], opn)} to {fmt_spread(game['home'], cur)})."))

    # 3. Model disagreement
    if alt_p_home is not None and abs(alt_p_home - p_home) >= 0.10:
        alt_fav, alt_pf = favored(game, alt_p_home)
        notes.append(("split", f"{alt_label} has {alt_fav} at {alt_pf:.0%}; the market has "
                               f"{alt_fav} at {(p_home if alt_fav == game['home'] else 1 - p_home):.0%}. "
                               f"A gap this size usually means news one side hasn't priced. Check injuries."))

    # 4. Crowd leverage (only if pick percentages are entered)
    if pick_pct is not None and fav in pick_pct:
        crowd = pick_pct[fav]
        if crowd >= 0.85 and pfav < 0.68:
            notes.append(("fade", f"{crowd:.0%} of your pool is on {fav}, but they win only {pfav:.0%} of the time. "
                                  f"If you need to gain ground, {dog} is the leverage pick."))

    # 5. Coin flip
    if pfav < 0.56:
        notes.append(("flip", "Coin flip. In a confidence pool this belongs in a low-point slot."))
    return notes


def yard_line(p_fav):
    """Field-position metaphor: the favorite is driving. 50% = midfield, 90% = opponent's 10."""
    return max(1, round(100 * (1 - p_fav)))
