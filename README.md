# Should I buy GPIX today?

A tiny GitHub Pages tool that answers one question every weekday morning: is today a
better-or-worse-than-average day to buy [GPIX](https://am.gs.com/en-us/advisors/funds/detail/PV109746/38151J286/goldman-sachs-s-p-500-core-premium-income-etf)
(Goldman Sachs S&P 500 Premium Income ETF)?

A sister page (`gpiq.html`) answers the same question for GPIQ, the Nasdaq-100 version
of the fund. It swaps in Nasdaq-specific inputs - VXN instead of VIX, QQQ instead of
SPY - and displays one extra check of its own: the **tech fear premium** (VXN/VIX ratio
ranked against its own past year). That check is context-only: the audit found its
intuitive scoring backwards, so it shows but never scores.

## How it decides

A GitHub Action runs every weekday morning, pulls data from Yahoo Finance, FRED, CNN's
Fear & Greed feed, and Google News, and evaluates ten transparent checks (eleven for
GPIQ). Under rules v2 only five rare conditions score, +1 each; everything else is
displayed as context with a score of 0:

| Signal | Source | v2 score | Notes |
| --- | --- | --- | --- |
| VIX vs its own past year (percentile) | Yahoo `^VIX` | **+1 at ≥p90** | The one vol band both audits found era-robust (+1.03pt/21d on SPY); lower bands are context |
| Discount from 52-week high (adjusted closes) | Yahoo `GPIX` | **+1 at ≥3%** | Measured with distributions reinvested so payouts don't masquerade as discounts; the old ≥7% "+2" was a dot-com artifact |
| VIX term structure (VIX/VIX3M) | Yahoo `^VIX3M` | **+1 at ≥1.00** | Inversion only; evidence mixed but it fires rarely and coincides with genuine panics |
| High-yield credit spread | FRED `BAMLH0A0HYM2` | **+1 at ≥5.0%** | Principled but untested - spreads never got this wide in the testable sample; the widening "−1" is retired (its test days rebounded) |
| Fear & Greed index | CNN | **+1 at ≤25** | Contrarian extreme; only ~1 year of testable history |
| Variance risk premium | VIX minus realized SPY vol | 0 (context) | Both scored directions era-flipped in the audits |
| GPIX vs its 50-day average (adjusted) | Yahoo `GPIX` | 0 (context) | The raw-close version was a distribution artifact; measured properly, still no edge |
| S&P 500 vs its 200-day average | Yahoo `SPY` | 0 (context) | The old below-200d "+1" was negative on both SPY and QQQ |
| Payout vs safe cash | Yahoo dividends + FRED `DGS3MO` | 0 (context) | Good to know, never a timing edge |
| Tech fear premium (GPIQ only) | Yahoo `^VXN`/`^VIX` | 0 (context) | The audit found the intuitive scoring backwards |
| Event calendar | Fed + BLS schedules | 0 (context) | Flags imminent FOMC decisions and CPI prints |

Each non-core source is individually guarded - if an endpoint is down, its signal shows
as skipped (score 0) instead of breaking the daily build. The composite score runs 0..+5
and maps to three honest answers: "better-than-usual entry" (≥3), "mild tailwind" (1-2),
and "no edge either way - buy on schedule" (0). The old "no discount today" band is
retired: two audits showed the tool couldn't spot bad days, so it stopped claiming to.

## Methodology v2 (post-audit)

Two independent empirical audits backtested every scoring band on long SPY/QQQ/VIX/VXN
history (forward 21- and 63-day total returns vs baseline, checked across eras) and
agreed: only a handful of rare conditions carry an era-robust positive edge, several of
the old bands were scored *backwards* (tech-fear-premium "+1", below-200d "+1", the deep
≥7% discount "+2"), and the rest were noise. Rules v2 keeps only the era-robust bands as
+1 scores, keeps two principled-but-untestable extremes (credit OAS ≥5%, Fear & Greed
≤25) with explicit honesty notes, demotes everything else to context, and eliminates
negative scores entirely - the tool flags rare good days and no longer pretends to spot
bad ones.

## The point of the backtest

The page also runs a running backtest: $100/week bought blindly vs $100/week held in cash
waiting for 3%+ dips, with distributions reinvested. It exists to keep the tool honest —
most of the time, the no-timing strategy wins, and the page says so out loud.

## Score history, report card, alerts

Each daily run appends the day's composite score to a per-fund history file
(`docs/history.json`, `docs/history-gpiq.json`). History was backfilled to each fund's
Oct 2023 inception by replaying the current rules on historical data (no lookahead in
the inputs: trailing percentiles and 52-week highs all end at each date; backfilled rows
are flagged). The files carry a `rules_version` stamp - when the rules change, old rows
are discarded and the whole history is regenerated under the new rules. CNN's Fear &
Greed history only reaches back ~1 year, so earlier days score that signal neutral.

The history powers three page features:

- **Report card** — for each verdict band, the average/median forward 21-trading-day
  total return of days that got that verdict, vs the all-days baseline, with honest
  caveats about overlapping windows and hindsight-designed rules.
- **Score timeline** — a colored strip under the price chart showing what the page
  would have said each day of the chart window.
- **"What would change this verdict"** — the nearest scoring thresholds (price for a
  3%+ adjusted discount, the vol index's p90 level, VIX-curve inversion, credit OAS 5%,
  Fear & Greed 25). All rows push the score up; nothing can push it down anymore.

`docs/feed.xml` is a combined Atom feed for both funds that only emits an entry when a
fund's verdict band changes from the prior day — subscribe via the "Alerts (RSS)" link
on either page.

## Development

```bash
python3 scripts/build_data.py   # regenerates docs/data.json + docs/data-gpiq.json (stdlib only)
python3 -m http.server -d docs  # view locally at http://localhost:8000
```

Pages serves from the `docs/` folder on `main`. The workflow in
`.github/workflows/update-data.yml` refreshes both data files each weekday at 13:35 UTC.

Not financial advice. Built as a personal decision aid.
