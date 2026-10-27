"""Builds docs/data/picks.json.

Live mode (default, used by GitHub Actions):
    python pipeline/build_picks.py
Manual mode (enter a week's lines yourself, or for testing):
    python pipeline/build_picks.py --manual config/manual_week.csv

Stdlib only, so the GitHub Action needs no installs.
"""
import argparse, csv, io, json, os, sys, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import model, ledger as led

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "picks.json")
SNAP_DIR = os.path.join(ROOT, "data", "snapshots")
RECORD = os.path.join(ROOT, "docs", "data", "record.json")
CONFIG = os.path.join(ROOT, "config")

NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NFLVERSE_INJURIES = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
ODDS_API = ("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
            "?regions=us&markets=h2h,spreads&oddsFormat=american&apiKey={key}")

# nflverse abbreviations are canonical in this project
ALIASES = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LA"}
FULL_NAMES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


def team(t):
    t = (t or "").strip().upper()
    return ALIASES.get(t, t)


def fnum(x):
    try:
        return float(x) if x not in (None, "", "NA") else None
    except ValueError:
        return None


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "field-position-picks"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------- Sources ----------------
def nflverse_games():
    rows = list(csv.DictReader(io.StringIO(fetch(NFLVERSE_GAMES))))
    return [r for r in rows if r.get("game_type") == "REG"]


def current_week(rows, season):
    upcoming = [int(r["week"]) for r in rows if int(r["season"]) == season and fnum(r["home_score"]) is None]
    return min(upcoming) if upcoming else None


def elo_ratings(rows):
    done = [r for r in rows if fnum(r["home_score"]) is not None and int(r["season"]) >= 2012]
    done.sort(key=lambda r: (r["gameday"], r["game_id"]))
    games = [{"season": int(r["season"]), "home": team(r["home_team"]), "away": team(r["away_team"]),
              "home_score": float(r["home_score"]), "away_score": float(r["away_score"]),
              "neutral": r.get("location") == "Neutral"} for r in done]
    return model.run_elo(games)


def records(rows, season):
    rec = {}
    for r in rows:
        if int(r["season"]) != season or fnum(r["home_score"]) is None:
            continue
        h, a, m = team(r["home_team"]), team(r["away_team"]), float(r["home_score"]) - float(r["away_score"])
        for t, res in ((h, m), (a, -m)):
            w, l, d = rec.get(t, (0, 0, 0))
            rec[t] = (w + (res > 0), l + (res < 0), d + (res == 0))
    return {t: f"{w}-{l}" + (f"-{d}" if d else "") for t, (w, l, d) in rec.items()}


def odds_api_lines():
    """Optional live lines. Median across books. Returns {(away, home): {...}}"""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return {}
    out = {}
    for ev in json.loads(fetch(ODDS_API.format(key=key))):
        h, a = FULL_NAMES.get(ev["home_team"]), FULL_NAMES.get(ev["away_team"])
        if not h or not a:
            continue
        mlh, mla, sph = [], [], []
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for o in mk.get("outcomes", []):
                    ab = FULL_NAMES.get(o["name"])
                    if mk["key"] == "h2h":
                        (mlh if ab == h else mla).append(o["price"])
                    elif mk["key"] == "spreads" and ab == h:
                        sph.append(o["point"])
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
        out[(a, h)] = {"ml_home": med(mlh), "ml_away": med(mla), "spread_home": med(sph)}
    return out


def injury_notes(season, week):
    """Key players listed Out or Doubtful. Returns {team: [str]}"""
    try:
        rows = csv.DictReader(io.StringIO(fetch(NFLVERSE_INJURIES.format(season=season))))
    except Exception as e:  # file may not exist yet for a new season
        print(f"injuries skipped: {e}", file=sys.stderr)
        return {}
    key_pos = {"QB": 3, "LT": 2, "RT": 1, "WR": 1, "CB": 1, "RB": 1, "TE": 1, "EDGE": 1, "DE": 1}
    out = {}
    for r in rows:
        if fnum(r.get("week")) != week or r.get("report_status") not in ("Out", "Doubtful"):
            continue
        pos = r.get("position", "")
        if pos in key_pos:
            out.setdefault(team(r["team"]), []).append((key_pos[pos], f"{r['full_name']} ({pos}) {r['report_status'].lower()}"))
    return {t: [s for _, s in sorted(v, reverse=True)[:3]] for t, v in out.items()}


# ---------------- Snapshots: passive line-movement tracking ----------------
def snapshot(season, week, games):
    """First time a game's line is seen this week becomes its 'open'. Stored in the repo."""
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, f"{season}-w{week:02d}.json")
    snap = load_json(path, {})
    for g in games:
        if g["id"] not in snap and g.get("spread_home") is not None:
            snap[g["id"]] = {"spread_home": g["spread_home"], "seen": datetime.now(timezone.utc).isoformat()}
    with open(path, "w") as f:
        json.dump(snap, f, indent=1)
    return snap


# ---------------- Assembly ----------------
def finish(games, season, week, alt_label, source):
    pool = load_json(os.path.join(CONFIG, "pool_lines.json"), {})   # {"AWAY@HOME": spread_home}
    picks = load_json(os.path.join(CONFIG, "pick_pct.json"), {})    # {"AWAY@HOME": {"KC": 0.9, ...}}
    out = []
    for g in games:
        key = f"{g['away']}@{g['home']}"
        if key in pool:
            g["pool_spread_home"] = float(pool[key])
        p = model.market_prob(g)
        if p is None:
            continue
        fav, pf = model.favored(g, p)
        g.update({
            "p_home": round(p, 4),
            "pick": fav, "pick_prob": round(pf, 4), "yard": model.yard_line(pf),
            "notes": [{"kind": k, "text": t} for k, t in
                      model.build_notes(g, p, g.get("alt_p_home"), alt_label, picks.get(key))],
        })
        out.append(g)
    out.sort(key=lambda g: (g.get("kickoff") or "", g["id"]))
    return {"season": season, "week": week, "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "alt_label": alt_label, "source": source, "games": out}


def build_live():
    rows = nflverse_games()
    season = max(int(r["season"]) for r in rows)
    week = current_week(rows, season)
    if week is None:
        raise SystemExit("No upcoming regular-season games found.")
    elo, rec = elo_ratings(rows), records(rows, season)
    live = odds_api_lines()
    inj = injury_notes(season, week)
    games = []
    for r in rows:
        if int(r["season"]) != season or int(r["week"]) != week:
            continue
        a, h = team(r["away_team"]), team(r["home_team"])
        sl = fnum(r.get("spread_line"))  # nflverse: positive = home favored
        g = {"id": r["game_id"], "kickoff": f"{r['gameday']}T{r.get('gametime') or '13:00'}",
             "away": a, "home": h, "away_record": rec.get(a, "0-0"), "home_record": rec.get(h, "0-0"),
             "spread_home": -sl if sl is not None else None,
             "ml_home": fnum(r.get("home_moneyline")), "ml_away": fnum(r.get("away_moneyline")),
             "status": "final" if fnum(r["home_score"]) is not None else "scheduled",
             "neutral": r.get("location") == "Neutral",
             "injuries": {t: inj.get(t, []) for t in (a, h) if inj.get(t)}}
        if (a, h) in live:
            g.update({k: v for k, v in live[(a, h)].items() if v is not None})
        g["alt_p_home"] = round(model.elo_expect(elo.get(h, 1500), elo.get(a, 1500), g["neutral"]), 4)
        games.append(g)
    snap = snapshot(season, week, games)
    for g in games:
        if g["id"] in snap:
            g["open_spread_home"] = snap[g["id"]]["spread_home"]
    src = "nflverse schedule and lines" + (", The Odds API live lines" if live else "") + ", Elo from nflverse results"
    data = finish(games, season, week, "Elo", src)
    # Track record: freeze picks at kickoff, grade finished games
    finals = {}
    for r in rows:
        hs, as_ = fnum(r["home_score"]), fnum(r["away_score"])
        if hs is not None and as_ is not None:
            finals[r["game_id"]] = team(r["home_team"]) if hs > as_ else team(r["away_team"]) if as_ > hs else "TIE"
    path = RECORD
    ledger = led.update(load_json(path, {}), season, week, data["games"], finals, datetime.now(timezone.utc))
    with open(path, "w") as f:
        json.dump(ledger, f, indent=1)
    for g in data["games"]:
        if g["id"] in finals:
            g["winner"] = finals[g["id"]]
    data["record"] = led.summary(ledger, season)
    data["history"] = history_line()
    return data


def history_line():
    """One-line context from the backtest, if it has been run."""
    bt = load_json(os.path.join(ROOT, "docs", "data", "backtest.json"), None)
    return bt.get("headline") if bt else None


def build_manual(path):
    games = []
    with open(path) as f:
        for r in csv.DictReader(f):
            a, h = team(r["away"]), team(r["home"])
            games.append({"id": r["id"], "kickoff": r["kickoff"], "away": a, "home": h,
                          "away_record": r.get("away_record", ""), "home_record": r.get("home_record", ""),
                          "spread_home": fnum(r.get("spread_home")), "ml_home": fnum(r.get("ml_home")),
                          "ml_away": fnum(r.get("ml_away")), "open_spread_home": fnum(r.get("open_spread_home")),
                          "alt_p_home": fnum(r.get("alt_p_home")), "status": r.get("status") or "scheduled",
                          "neutral": r.get("neutral", "").lower() == "true", "injuries": {}})
    meta = load_json(os.path.join(CONFIG, "manual_meta.json"), {})
    return finish(games, meta.get("season"), meta.get("week"), meta.get("alt_label", "Second model"),
                  meta.get("source", "Manually entered lines"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", help="CSV of this week's lines instead of fetching")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    data = build_manual(args.manual) if args.manual else build_live()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(data, f, indent=1)
    print(f"wrote {len(data['games'])} games for week {data['week']} to {args.out}")


if __name__ == "__main__":
    main()
