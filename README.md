# D2 Trader

Tracks Diablo II: Resurrected item prices per market, establishes a fair price range,
and tells you when something you watch reaches your price. Everything is built around
ladder resets, which move prices more than anything else in the game.

Generated from [devkit](https://github.com/alexandrec90/devkit)'s project
template. The agent harness in `scripts/hooks/` is vendored from there — see
`CLAUDE.md`, "Vendored agent harness".

## Quick start

```bash
cp .env.example .env          # then set COMPOSE_PROJECT_NAME and anything else you want
docker compose up -d --build  # database, migrations, dashboard, collector
```

Then open <http://127.0.0.1:8010>. The first catalog sync takes about six minutes,
because diablo2.io is fetched at one page a minute; until it finishes, no item can be
added to a watchlist. `/sources` shows what each collector is doing.

For development:

```bash
uv sync --all-extras          # creates .venv from the committed uv.lock
docker compose up -d db
pytest                        # applies migrations itself
python scripts/e2e.py         # do the parsers still understand the live sites?
python -m d2_trader run seasons   # one collection job, now
```

## Data sources

Read this before changing what the collectors do. Each source's rules were checked on
2026-11-06, and they differ.

| Source | What it gives | How it is reached |
| --- | --- | --- |
| Blizzard news | The official season schedule | Automatic, every 6 hours |
| diablo2.io | Item catalog; last 10 sales per item, 3 days late | Automatic, daily, inside its robots.txt, one page a minute |
| Traderie | Live asks, bids and completed trades, per market | Automatic, hourly, when `TRADERIE_ENABLED=true` |
| d2jsp | Forum gold prices | **By hand only**, on the Import page |

**Traderie is on, by the operator's decision.** Its
[Terms of Service](https://traderie.com/termsofservice), section 4, say you agree not to
"use any robot, spider, or other automatic device, process, or means to access the
website for any purpose, including monitoring or copying any of the material". The
operator turned collection on anyway on 2026-11-07, for personal, non-commercial use,
accepting the risk: an IP block or action on the Traderie account. It is off in code
(`config.py`), so a fresh clone sends nothing until `.env` says so. Its API is
undocumented, and could change without notice.

**d2jsp is never fetched.** Its [rules](https://forums.d2jsp.org/info.php?p=9) say "No
botting or automation of any features within d2jsp", including page refreshing, and its
forums sit behind a bot challenge. Read a thread yourself and paste the lines into the
Import page. A pasted rune price list is also what measures the forum gold rate.

**A site that refuses this app is left alone.** `d2_trader/sources/http.py` is the only
HTTP path. It honours robots.txt, identifies itself honestly, and waits a random gap
between requests: the site's minimum stretched by 0-100% (`PACE_JITTER`), with each poll
taking its items in a fresh order. That spreads the load; it is not a disguise. It stops
on a 403 or a challenge page, with no retries, and a blocked job waits four times its
interval. Do not add a browser user agent, a proxy or a challenge solver: a refusal is an
answer.

## Forum gold

d2jsp prices in forum gold, which has no fixed value, so it is measured through runes,
the one thing priced on both sides:

1. Every fg price of a rune is one reading: the rune's value in Ist from Traderie's
   rune-for-rune trades, over the fg it went for.
2. All readings, from every rune and every day, feed the price model below. The Rates
   page shows the rate today with its 95% interval, its history, and the last week's
   readings by rune.
3. Every fg price is valued at the model's rate for its own day.

So keep it fed: paste a d2jsp rune price list now and then (Import, "Rune price list").
Between pastes the rate carries on, its interval widening to say how stale it is.
Setting `fg` by hand on the Rates page pins it instead, until cleared.

## Live pages

Every page checks for new data every 15 seconds and redraws itself when anything it shows
has changed, and every 5 minutes anyway so countdowns move. It holds off while you are
typing in a form, have a panel open, or are hovering over a chart. The header says
"Live", or why it is waiting.

The dashboard's footer credits diablo2.io, as the site asks of users of its public API.
That request was made for the API, and this app does not use it, but it costs nothing.

## How resets are handled

Prices are only compared within one **market**: platform, softcore or hardcore, ladder or
non-ladder, and game version. Sources are combined per market, and a ladder price is
never blended with a non-ladder one.

**The calendar.** Blizzard publishes each schedule in an article, in a stable format:
"Ladder Season 15 Ends: Monday, November 9, 11 a.m. PST". The app reads that, and
cross-checks it against the announcement on diablo2.io's front page. If the two disagree,
or Blizzard's article cannot be read, you get a notification instead of a silent wrong
date. A date entered on the Seasons page outranks every source. An end nobody has
announced is estimated from past season lengths, and is labelled as an estimate wherever
it appears.

**The phases**, and what each does to prices:

| Phase | When | Recent-sales window |
| --- | --- | --- |
| Launch | First 14 days | Last 3 days of this season. Prices are high and falling |
| Early | Up to day 35 | Last 7 days of this season |
| Mid | Until the end is near | Last 14 days |
| Season ending | Last 21 days | Last 5 days. Prices are sliding |
| Between seasons | End to next start | **Closed:** no range, and alerts pause |

The estimated price uses every sale this season, not just the window (see below).

Three rules follow, and `tests/test_seasons.py` and `tests/test_watch.py` hold them:

- A ladder price never includes a sale from an earlier season.
- Ladder alerts never fire between seasons, when cheap stale listings would look like
  bargains.
- A non-ladder window restarts at each reset, because ladder items have just moved there.

Alert messages carry the seasonal context, so a low price in the last week of a season
reads as what it is. You also get notified when dates are announced, 3 days before a
season ends, when it ends, a day before the next starts, and when it starts.

## How a price is worked out

D2R has no money, so every price is valued in **Ist runes**. A listing may offer
alternatives ("Ber or 2 Lo"): a seller is held to the cheapest, a buyer to the best. A
lot is divided per item. Rune rates start from values measured on 2026-11-06, are
re-measured per market every 12 hours from Traderie's rune-for-rune trades, and can be
overridden on the Rates page. Forum gold is measured, as above. A listing paid in
something with no rate is left out rather than guessed.

**The estimated price** (and the forum gold rate) come from one model, in `trend.py`.
Prices are not independent from day to day: today's is yesterday's plus a step, and the
steps lean one way for a while. So a price is a hidden level moving as a random walk
with drift, seen through noisy quotes, and fitted with a Kalman filter and smoother:

- Every sale counts. One far from the rest is down-weighted (the further, the less),
  never dropped, and every day's estimate uses the quotes before and after it.
- A day with no trades still has an estimate; its interval widens with the gap.
- At a reset the price may jump, so the model relearns it rather than averaging across.
- The noise levels are fitted to the data, and doubt about them widens the interval.
- One stated assumption: a market price moves at least about 1% a day. Without it the
  fit, on a few months of data, too often decides a price never moves, and overstates
  its confidence.

Checked on simulated random walks where the true price is known (`tests/test_trend.py`):
the 95% interval held the truth on 92-99% of days with clean quotes, and 86-98% when
one quote in twenty was a bait or joke price.

The **recent sales** range beside it is the median and middle half of the window's
sales. Lowest ask and highest bid still ignore bait listings, because a single fake ask
is exactly what an alert must not fire on. Everything is computed from raw listings at
read time, so correcting a rate or a date corrects history.

Known limit: rolls are not priced. A perfect and a low-roll copy of an item land in one
range. Ethereal and non-ethereal copies can be separated per watch.

## Notifications

The dashboard's Inbox always works, and the Inbox page can turn on browser notifications
for while a tab is open. To reach your phone, set `DISCORD_WEBHOOK_URL` or `NTFY_URL` in
`.env` and restart the stack. A rule fires once per crossing, rearms when the price moves
3% back past the line, and honours a quiet period you choose.

The dashboard has no login and binds to loopback only. Keep it that way.

In VS Code, `Ctrl+Shift+B` runs the default build task and the task quick-pick
(`Ctrl+Shift+P` → "Run Task") lists everything else, each with a one-line `detail`
explaining what it costs and what it touches.

## Host ports

This checkout is **slot 10** in devkit's `ports.toml`. Every published port
is its conventional base plus the slot:

| Service | Host port |
| --- | --- |
| app | 8010 |
| postgres | 5442 |

Regenerate the `.env` block for any checkout with
`python <devkit>/scripts/devkit_ports.py <checkout-name>`.

## Layout

```text
d2_trader/               application code
  seasons.py             the reset calendar, phases and price windows (pure)
  stats.py alerts.py     price ranges and alert decisions (pure)
  currency.py markets.py valuing prices in Ist, and what a market is (pure)
  sources/               one module per external site; http.py is the only HTTP path
  services/              everything that combines the database with the above
  web/                   the dashboard
tests/                   tests; test_live_sites.py is the local tier scripts/e2e.py runs
scripts/                 project scripts (Python, each with tests)
scripts/hooks/           vendored agent harness — edit upstream in devkit
.devkit.toml      the per-project harness seam (NOT vendored)
docker-compose.yml       the local stack
```

## CI

`.github/workflows/pr-gate.yml` runs lint, tests, and the harness drift check on
every PR. The drift check is only meaningful when it can see devkit — if it prints
"nothing to do (skipping)", the gate is inert and the wiring needs fixing. Locally,
where `DEVKIT_DIR` is usually unset, `pre-commit run devkit-drift --all-files` is the
same check against the devkit rev pinned in `.pre-commit-config.yaml`.

`.github/dependabot.yml` opens weekly dependency PRs, and
`.github/workflows/dependabot-automerge.yml` merges every one of them once the gate
passes, majors included: the gate is the review, so keep it covering what you care
about. To hold one bump back, close it or comment `@dependabot ignore this major
version`: a removed `automerge` label comes back on Dependabot's next rebase.

That auto-merge workflow is **vendored** from devkit, so `sync-devkit.py --check`
compares it byte-for-byte and editing it here shows up as drift. Keep the gate's title
`PR Gate`: the auto-merge workflow waits on it by name, and a rename makes the merge job
inert without turning anything red.

`.github/actions/setup-python-env/` is **yours**, by contrast. devkit shipped it through
that same channel for one release and pulled it back out when two projects turned out to
install in ways `uv sync` cannot express, so it now arrives from `templates/` as a
one-shot copy: nothing compares it, and rewriting its steps to match how this project
installs is the supported move rather than drift. What owning it costs is the other
direction — later devkit fixes to that file never reach here.
