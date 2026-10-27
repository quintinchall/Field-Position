"""Track record with no hindsight.
A game's pick is frozen at the last run before its kickoff. Games first seen after kickoff are never added.
Results are filled in from final scores. Stored in docs/data/record.json."""
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def kickoff_dt(kickoff):
    """nflverse kickoffs are Eastern local time, 'YYYY-MM-DDTHH:MM'; manual ones may carry an offset."""
    d = datetime.fromisoformat(kickoff)
    return d if d.tzinfo else d.replace(tzinfo=ET)


def update(ledger, season, week, games, finals, now):
    """ledger: {"<season>": {"<week>": {game_id: {...}}}}
    games: this week's picks. finals: {game_id: winner_abbr or "TIE"}. now: aware datetime."""
    wk = ledger.setdefault(str(season), {}).setdefault(str(week), {})
    for g in games:
        if kickoff_dt(g["kickoff"]) > now:          # still before kickoff: (re)lock the current pick
            wk[g["id"]] = {"away": g["away"], "home": g["home"], "pick": g["pick"], "prob": g["pick_prob"],
                           "locked_at": now.isoformat(timespec="minutes")}
    for weeks in ledger.values():                     # grade anything that has finished
        for entries in weeks.values():
            for gid, e in entries.items():
                if "winner" not in e and gid in finals:
                    e["winner"] = finals[gid]
    return ledger


def summary(ledger, season):
    weeks, W, L = [], 0, 0
    for wk, entries in sorted(ledger.get(str(season), {}).items(), key=lambda x: int(x[0])):
        w = sum(1 for e in entries.values() if e.get("winner") not in (None, "TIE") and e["winner"] == e["pick"])
        l = sum(1 for e in entries.values() if e.get("winner") not in (None, "TIE") and e["winner"] != e["pick"])
        if w + l:
            weeks.append({"week": int(wk), "w": w, "l": l})
            W, L = W + w, L + l
    return {"season": season, "w": W, "l": L, "weeks": weeks}
