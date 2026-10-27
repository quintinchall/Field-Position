"""Backtest: how would the market pick, the Elo pick, and the disagreement flag have done?
Run: python pipeline/backtest.py      (writes docs/data/backtest.json and a summary table)"""
import csv, io, json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import model
from build_picks import fetch, fnum, team, NFLVERSE_GAMES, ROOT

FIRST_ELO_SEASON, FIRST_TEST_SEASON = 2012, 2015  # three seasons of Elo warm-up before grading


def load_rows():
    rows = [r for r in csv.DictReader(io.StringIO(fetch(NFLVERSE_GAMES))) if r.get("game_type") == "REG"]
    rows = [r for r in rows if int(r["season"]) >= FIRST_ELO_SEASON and fnum(r["home_score"]) is not None]
    rows.sort(key=lambda r: (r["gameday"], r["game_id"]))
    return rows


def run(rows):
    ratings, season, graded = {}, None, []
    for r in rows:
        s = int(r["season"])
        if s != season:
            if season is not None:
                ratings = {t: 1500 + (x - 1500) * (1 - model.ELO_REVERT) for t, x in ratings.items()}
            season = s
        h, a = team(r["home_team"]), team(r["away_team"])
        neutral = r.get("location") == "Neutral"
        rh, ra = ratings.get(h, 1500.0), ratings.get(a, 1500.0)
        p_elo = model.elo_expect(rh, ra, neutral)  # computed BEFORE the game is played
        sl = fnum(r.get("spread_line"))
        g = {"ml_home": fnum(r.get("home_moneyline")), "ml_away": fnum(r.get("away_moneyline")),
             "spread_home": -sl if sl is not None else None}
        p_mkt = model.market_prob(g)
        margin = float(r["home_score"]) - float(r["away_score"])
        if s >= FIRST_TEST_SEASON and p_mkt is not None and margin != 0:
            graded.append({"season": s, "week": int(r["week"]), "p_mkt": p_mkt, "p_elo": p_elo, "home_won": margin > 0})
        ratings[h], ratings[a] = model.elo_update(rh, ra, margin, neutral)
    return graded


def score(games, key):
    if not games:
        return {"n": 0}
    hits = sum((g[key] >= 0.5) == g["home_won"] for g in games)
    brier = sum((g[key] - g["home_won"]) ** 2 for g in games) / len(games)
    return {"n": len(games), "correct": hits, "pct": round(hits / len(games), 4), "brier": round(brier, 4)}


def summarize(graded):
    out = {"seasons": {}, "overall": {}, "disagree": {}, "calibration": []}
    for s in sorted({g["season"] for g in graded}):
        gs = [g for g in graded if g["season"] == s]
        out["seasons"][s] = {"market": score(gs, "p_mkt"), "elo": score(gs, "p_elo")}
    out["overall"] = {"market": score(graded, "p_mkt"), "elo": score(graded, "p_elo")}
    # The flag the app shows: Elo and market at least 10 points apart
    dis = [g for g in graded if abs(g["p_elo"] - g["p_mkt"]) >= 0.10]
    flip = [g for g in dis if (g["p_elo"] >= 0.5) != (g["p_mkt"] >= 0.5)]
    out["disagree"] = {"gap_10": {"market": score(dis, "p_mkt"), "elo": score(dis, "p_elo")},
                       "different_winner": {"market": score(flip, "p_mkt"), "elo": score(flip, "p_elo")}}
    # Calibration: when the market said X%, how often did the favorite win?
    for lo in (0.5, 0.6, 0.7, 0.8, 0.9):
        b = [g for g in graded if lo <= max(g["p_mkt"], 1 - g["p_mkt"]) < lo + 0.1]
        if b:
            fav_won = sum((g["p_mkt"] >= 0.5) == g["home_won"] for g in b)
            avg = sum(max(g["p_mkt"], 1 - g["p_mkt"]) for g in b) / len(b)
            out["calibration"].append({"bucket": f"{int(lo*100)}-{int(lo*100)+10}%", "n": len(b),
                                       "predicted": round(avg, 4), "actual": round(fav_won / len(b), 4)})
    return out


def rec(x):
    return f"{x['correct']}-{x['n'] - x['correct']} ({x['pct']:.1%})"


def headline(o):
    """Completed seasons only, so a half-played season can't skew the benchmark."""
    full = [s for s, v in o["seasons"].items() if v["market"]["n"] >= 250]
    if not full:
        return None
    n = sum(o["seasons"][s]["market"]["n"] for s in full)
    c = sum(o["seasons"][s]["market"]["correct"] for s in full)
    return {"from": min(full), "to": max(full), "games": n, "fav_pct": round(c / n, 4)}


def markdown(o):
    h, d = o["headline"], o["disagree"]["different_winner"]
    L = ["## Backtest", ""]
    if h:
        L.append(f"**Picking the betting favorite, {h['from']} to {h['to']}:** won {h['fav_pct']:.1%} of {h['games']} games.")
    if d["market"]["n"]:
        L.append(f"**When Elo and the favorite picked different winners** ({d['market']['n']} games): "
                 f"favorite right {d['market']['pct']:.1%}, Elo right {d['elo']['pct']:.1%}.")
    L += ["", "| Season | Pick the favorite | Pick Elo |", "|---|---|---|"]
    for s, v in o["seasons"].items():
        tag = "" if v["market"]["n"] >= 250 else " (so far)"
        L.append(f"| {s}{tag} | {rec(v['market'])} | {rec(v['elo'])} |")
    L += ["", "Uses closing lines, so it slightly flatters the favorite compared with picking earlier in the week."]
    return "\n".join(L)


def main():
    o = summarize(run(load_rows()))
    o["headline"] = headline(o)
    path = os.path.join(ROOT, "docs", "data", "backtest.json")
    with open(path, "w") as f:
        json.dump(o, f, indent=1)
    md = markdown(o)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")


if __name__ == "__main__":
    main()
