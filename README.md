# Field Position

NFL pick'em helper. A scheduled GitHub Action pulls lines, results, and injury reports, computes win chances, and publishes a page your friends open before locking picks.

## What it does
- **Win chance** for every game from sportsbook moneylines with the vig removed (spread used as fallback).
- **Second opinion** from an Elo rating built on every game since 2012. A big gap between Elo and the market gets flagged.
- **Line movement**: the first line the bot sees each week is saved as the "open." Later moves and flips are flagged.
- **Pool line gaps**: enter your pool's locked spreads in `config/pool_lines.json` and it flags games where the market has moved away from them. In spread pools this is the strongest signal you'll get.
- **Crowd leverage**: enter your pool's pick percentages in `config/pick_pct.json` and it flags overloaded favorites.
- Three views: straight up, confidence ranking, survivor (with a used-teams tracker saved in the viewer's browser).

## Setup (about 15 minutes)
1. Create a GitHub account, make a new public repository, and upload everything in this folder (keep the folder structure, including `.github`).
2. Settings, then Pages: deploy from branch `main`, folder `/docs`. Your link will be `https://<username>.github.io/<repo>/`.
3. Actions tab: enable workflows, open "Update picks", click "Run workflow". It also runs automatically Tuesday through Saturday at 8am and 6pm Eastern and Sunday at 10am.
4. Optional live odds: get a free key at the-odds-api.com, then Settings, Secrets and variables, Actions, new secret named `ODDS_API_KEY`. Without it the bot uses nflverse lines, which update less often.

## Weekly inputs (optional, 5 minutes)
`config/pool_lines.json`
```json
{"LAC@KC": -7.5, "DAL@GB": 1.0}
```
Spread is from the home team's side: -7.5 means home favored by 7.5.

`config/pick_pct.json`
```json
{"LAC@KC": {"KC": 0.94, "LAC": 0.06}}
```

## Manual mode
If a data source breaks, fill in `config/manual_week.csv` and run:
```
python pipeline/build_picks.py --manual config/manual_week.csv
```

## Tests
`python tests/test_all.py` runs before every build. If a test fails, the page keeps last good data.

## Things to verify on the first live run
- The nflverse `games.csv` sign convention is handled as "positive spread_line = home favored" and tested against that assumption. Spot-check one game against a sportsbook after the first run.
- The injuries file for the current season may not exist early in the season; the bot skips it and logs a message.
- Team abbreviations follow nflverse (`LA` for the Rams, `JAX` for Jacksonville).
