# Should I buy GPIX today?

A tiny GitHub Pages tool that answers one question every weekday morning: is today a
better-or-worse-than-average day to buy [GPIX](https://am.gs.com/en-us/advisors/funds/detail/PV109746/38151J286/goldman-sachs-s-p-500-core-premium-income-etf)
(Goldman Sachs S&P 500 Premium Income ETF)?

A sister page (`gpiq.html`) answers the same question for GPIQ, the Nasdaq-100 version
of the fund. It swaps in Nasdaq-specific inputs - VXN instead of VIX, QQQ instead of
SPY - and displays one extra check of its own: the **tech fear premium** (VXN/VIX ratio
ranked against its own past year). That check is context-only: the audit found its
intuitive scoring backwards, so it shows but never scores.

Four single-stock pages (`tsla.html`, `spcx.html`, `nvda.html`, `goog.html`) extend the
same machinery to Tesla, SpaceX, Nvidia and Alphabet (Class C) with an asset-aware
engine - see "Single-stock pages" below. Every one of them scores only what *its own*
history proved: TSLA all five single-stock bands (including the project's one
evidence-backed *negative* zone), NVDA two, GOOG one, and SPCX (IPO June 2026) none at
all - it pins at 50 until enough history accumulates to test anything.

The pipeline runs **twice per weekday** (13:35 and 19:15 UTC): a morning refresh and
an early-afternoon one so the buy score is fresh for the buy-at-close window. The
engine updates today's history row in place on the second run, so histories keep one
row per day. Fund JSONs also carry a `distributions` block (last 24 payouts, TTM sum,
and an estimated next ex-date projected from the payout cadence) which feeds the
"What it actually pays" bar chart on the GPIX/GPIQ pages.

A fifth page (`retire.html`) answers "when can I retire?" for a fixed savings plan
($2,500 biweekly into 80% GPIX / 20% GPIQ by default, all adjustable). It reads the
funds' live TTM yields from `data.json`/`data-gpiq.json`, simulates monthly
contributions with after-tax (15% US withholding) reinvestment under three NAV-drift
scenarios, and charts when the net payout crosses the target monthly income. Pure
client-side - no new data files or workflow steps.

## How it decides

A GitHub Action runs every weekday morning, pulls data from Yahoo Finance, FRED, CNN's
Fear & Greed feed, and Google News, and evaluates eleven transparent checks (twelve for
GPIQ). Under rules v4 only six rare conditions score, each weighted by the effect size
the validation work measured (see "Rules v4" below); everything else is displayed as
context with a score of 0:

| Signal | Source | v4 weight | Notes |
| --- | --- | --- | --- |
| Short-term pullback (reversal) on the underlying | Yahoo `SPY`/`QQQ` | **+2.0 at 3+ down closes or 5-session return ≤ −3%** | The strongest validated edge; see below |
| Discount from 52-week high (adjusted closes) | Yahoo `GPIX` | **+1.5 at ≥3%** | Measured with distributions reinvested so payouts don't masquerade as discounts; the old ≥7% "+2" was a dot-com artifact |
| VIX vs its own past year (percentile) | Yahoo `^VIX` | **+1.5 at ≥p90** | The one vol band both audits found era-robust (+1.03pt/21d on SPY); lower bands are context |
| VIX term structure (VIX/VIX3M) | Yahoo `^VIX3M` | **+1.0 at ≥1.00** | Inversion only; evidence mixed but it fires rarely and coincides with genuine panics |
| High-yield credit spread | FRED `BAMLH0A0HYM2` | **+1.0 at ≥5.0%** | Principled but untested - spreads never got this wide in the testable sample; the widening "−1" is retired (its test days rebounded) |
| Fear & Greed index | CNN | **+1.0 at ≤25** | Contrarian extreme; only ~1 year of testable history |
| Variance risk premium | VIX minus realized SPY vol | 0 (context) | Both scored directions era-flipped in the audits |
| GPIX vs its 50-day average (adjusted) | Yahoo `GPIX` | 0 (context) | The raw-close version was a distribution artifact; measured properly, still no edge |
| S&P 500 vs its 200-day average | Yahoo `SPY` | 0 (context) | The old below-200d "+1" was negative on both SPY and QQQ |
| Payout vs safe cash | Yahoo dividends + FRED `DGS3MO` | 0 (context) | Good to know, never a timing edge |
| Tech fear premium (GPIQ only) | Yahoo `^VXN`/`^VIX` | 0 (context) | The audit found the intuitive scoring backwards |
| Event calendar | Fed + BLS schedules | 0 (context) | Flags imminent FOMC decisions and CPI prints |

Each non-core source is individually guarded - if an endpoint is down, its signal shows
as skipped (score 0) instead of breaking the daily build. The weighted composite W runs
0..8 and maps to three honest answers: "better-than-usual entry" (W ≥ 3), "mild
tailwind" (W ≥ 1), and "no edge either way - buy on schedule" (W = 0). The old "no
discount today" band is retired: two audits showed the tool couldn't spot bad days, so
it stopped claiming to.

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

## Rules v3: the reversal signal and the execution note

A literature-mining project (393 papers, ~40 candidate rules backtested on SPY 1993-2026
and QQQ 1999-2026 at audit grade: era-robustness required, t-stats on effective sample
sizes) produced exactly two edges that passed. Both are now in the tool:

- **Short-term index reversal (scored, +1).** When the fund's underlying proxy (SPY for
  GPIX, QQQ for GPIQ - the validation was done on the underlying, not the fund) has, on
  adjusted closes, closed down 3+ consecutive sessions or fallen ≥3% over 5 sessions,
  the next day has been reliably above average: SPY +0.21% next-day excess (t=3.6), QQQ
  +0.39% (t=4.0), positive in every era including 2016+, and additive on days when no
  other signal fires. It's a classic liquidity-provision reversal (Park 1995;
  Chordia-Roll-Subrahmanyam 2002) and fires roughly 25-35 days a year.
- **Overnight premium (execution note, not scored).** Nearly all index return has
  historically accrued overnight, not intraday (Lou-Polk-Skouras 2019; our validation:
  SPY +3.3bp/night t=4.6, QQQ +5.3bp t=4.9, positive in every era). Both pages carry a
  one-liner: if you're buying, place the order near today's close rather than tomorrow's
  open. Basis points per night - stated because it's free, not because it's dramatic.

## Rules v4: evidence-weighted signals and the 0–100 buy score

v4 keeps the same six validated conditions but weights each by the effect size the
validation work actually measured, instead of a flat +1:

| Condition | Weight | Evidence basis |
| --- | --- | --- |
| Short-term reversal | **2.0** | Strongest validated edge: SPY +0.21%/1d excess (t=3.6), QQQ +0.39%/1d (t=4.0), era-robust everywhere |
| Discount ≥3% (adjusted) | **1.5** | +0.7–0.9pt/21d, era-stable on both indexes |
| Vol index ≥ p90 | **1.5** | +1.0pt/21d on SPY, era-positive |
| VIX/VIX3M inversion | **1.0** | Mixed evidence, rare, panic-coincident |
| Credit OAS ≥ 5% | **1.0** | Principled, untestable in the available sample |
| Fear & Greed ≤ 25 | **1.0** | Principled, ~1 year of testable history |

The weighted composite W ranges 0..8 and maps to the headline **buy score**:

```
score100 = round(50 + W × 6.25)
```

50 on a typical day, 100 when everything fires. The score never goes below 50 by
design — two audits found no reliable negative signal, so the tool doesn't claim any —
and both pages say so explicitly, so 50 doesn't read as "half-bad". Verdict bands sit
on W: **good** at W ≥ 3 (score ≥ 69 — e.g. reversal + discount, W 3.5 → 72), **ok** at
W ≥ 1 (score 56–68 — any single condition; reversal alone is W 2 → 62), **neutral** at
W = 0 (score 50). `verdict.score` in the JSON stays the weighted W for compatibility;
`verdict.score100` and `verdict.weights_max` (8) are new, each signal carries its
`weight` (0 for context signals), and both history files were regenerated under
`rules_version` 4 with per-day `score` (W) and `score100`. The fund rules are unchanged
in v5, which only touches the single-stock pages (see below); the version bump still
regenerates every history file, as designed.

## Single-stock pages: TSLA, SPCX, NVDA, GOOG

Single stocks are not index funds, and the engine treats them differently
(`kind: "stock"` in the `FUNDS` config): index-validated bands were **not** assumed to
transfer. TSLA got its own audit-grade validation pass (2010-2026, ~4,050 sessions,
same forward 21/63-day excess-return method, four era splits, effective-N t-stats)
before anything was allowed to score. The results inverted several index intuitions:

| TSLA condition | Weight | Evidence (all-era robust unless noted) |
| --- | --- | --- |
| Crash pullback: 5-session return ≤ −12% | **+2.0** | +0.87pt next-day excess (t=2.2), +11.2pt/63d, positive in every era at every horizon (~11 days/yr) |
| 3 consecutive down closes | **+1.0** | +0.35pt/1d (t=1.7), all eras positive at 1d; longer horizons mixed, hence half weight |
| 20d realized vol ≥ p90 of its year | **+1.5** | +0.35pt/1d (t=1.7) and +15.5pt/63d, all eras positive (no keyless TSLA IV index exists; realized vol substitutes) |
| Within 0.5% of 52-week high | **+1.0** | +0.62pt/1d (t=2.05), all eras — single-name **momentum**, the opposite of the index result |
| Drawdown 10–20% ("falling knife") | **−1.5** | Negative in every era at 1d (t=−2.6) and 21d (t=−1.9) — the one evidence-backed worse-than-average zone in this project |
| Drawdown ≥35% ("deep value") | 0 (context) | +13pt/63d but era-fragile (t=1.1); narrowly missed the bar |
| Index-style 3–7% discount | 0 (context) | Does not transfer — noise on TSLA |
| Market-wide VIX/F&G panic bands | 0 (context) | Era-flipped when tested against TSLA forward returns |

Because of the falling-knife band, **TSLA's buy score can fall below 50** (floor 41,
verdict band "worse-than-average zone" at W ≤ −1) - the only page where the tool
claims a bad day, because the evidence there was unambiguous. Its meter runs 40-73
accordingly, and its report card carries a fourth "caution" row. Note the meter tops
out at 73 rather than at the arithmetic sum of the positive weights (4.5): a 12%
five-session crash leaves the price at least 12% under its 52-week high, so the crash
and at-the-high bands can never fire together and the real ceiling is W 3.5 (score 72).
`verdict.weights_max` reports the reachable ceiling, not the naive sum.

SPCX (Space Exploration Technologies Corp., first traded 2026-06-12) has ~2 months of
history: no 52-week high, no 200-day average, no 1-year percentile, no validation
sample. Nothing scores; the score pins at 50 with on-page copy saying that's a
statement about the tool's knowledge, not about SpaceX. Cards render honest "too
young" notes with the dates each measure unlocks (50d SMA ~Aug 2026, 200d ~Mar 2027,
1-year signals Jun 2027). Both stock pages also carry a single-stock honesty block:
the "any day is fine" floor was validated on diversified indexes and does not protect
against company-specific impairment - position sizing beats timing for single names.

### NVDA and GOOG: the framework did not transfer

NVDA and GOOG shipped in rules v4 on TSLA's weights, on the assumption that a
single-stock framework generalises between single stocks. Running the same
audit-grade test on their own histories (`scripts/validate_stock_bands.py`, Nasdaq
daily closes 2016-2026, forward 1/21/63-day excess returns, effective-N t-stats, four
era splits) showed it does not. Three of TSLA's five bands come out with the **wrong
sign** on the new tickers:

| Band | TSLA sign | NVDA (1d excess) | GOOG (1d excess) |
| --- | --- | --- | --- |
| Crash pullback (5d ≤ −12%) | positive | **+1.30pt, all four eras — scores 2.0** | 10 events in a decade — untestable, no score |
| 3 consecutive down closes | positive | **+0.76pt, all four eras — scores 1.0** | **+0.23pt, all four eras — scores 1.0** |
| 20d realized vol ≥ p90 | positive | −0.12pt, and −6.0pt/21d below baseline in *every* era | −0.19pt, below baseline in every era |
| Within 0.5% of 52-week high | positive | +0.04pt, era-split | −0.07pt, below baseline in all four eras |
| Drawdown 10–20% ("falling knife") | **negative** | **+0.19pt — above baseline** | **+0.01pt — at or above baseline** |

The falling knife is the one that mattered most: it is the project's only negative
band, and on NVDA and GOOG those days ran *above* average, so v4 was docking points
for a decent setup. On 2026-08-31 that produced a GOOG verdict of "worse-than-average
zone, 41/100" on a day the page's own report card showed the caution band beating
baseline. Under rules v5 each ticker carries its own `bands` tuple in `FUNDS` and only
those bands score. Rejected bands still render as cards, annotated with the numbers
that rejected them. NVDA and GOOG therefore have no negative band and floor at 50 like
the funds, with meters of 50–63 and 50–57 (their reachable ceilings) instead of TSLA's
40–73.

The same script is how any future ticker earns its weights - run it, put what passes in
`bands`, and put what fails in `band_failed` so the page can say why.

Stock pages add **earnings dates** to the event calendar (Yahoo quoteSummary
`calendarEvents` via its cookie+crumb handshake - keyless, guarded, omitted with a
note if the endpoint breaks). Earnings within 5 days flags as the dominant
single-stock volatility event. The handshake's first hop primes a cookie from
`fc.yahoo.com`, which answers **404** while still setting the session cookie; running
that request under `curl --fail` turns the 404 into a hard error and kills the whole
handshake, so that one call deliberately omits `--fail`.

## The point of the backtest

The page also runs a running backtest: $100/week bought blindly vs $100/week held in cash
waiting for 3%+ dips, with distributions reinvested. It exists to keep the tool honest —
most of the time, the no-timing strategy wins, and the page says so out loud.

## Score history, report card, alerts

Each daily run appends the day's composite score to a per-asset history file
(`docs/history.json`, `docs/history-gpiq.json`, `docs/history-tsla.json`,
`docs/history-spcx.json`, `docs/history-nvda.json`, `docs/history-goog.json`). History
was backfilled to Oct 2023 (the funds' inception, also used for TSLA/NVDA/GOOG so the
report cards cover the same window; SPCX backfills from its June 2026 IPO) by replaying
the current rules on historical data (no lookahead in the inputs: trailing percentiles
and 52-week highs all end at each date; backfilled rows are flagged). The files carry a
`rules_version` stamp - when the rules change, old rows are discarded and the whole
history is regenerated under the new rules. CNN's Fear & Greed history only reaches
back ~1 year, so earlier days score that signal neutral.

The history powers three page features:

- **Report card** — for each verdict band, the average/median forward 21-trading-day
  total return of days that got that verdict, vs the all-days baseline, with honest
  caveats about overlapping windows and hindsight-designed rules.
- **Score timeline** — a colored strip under the price chart showing what the page
  would have said each day of the chart window.
- **"What would change this verdict"** — the nearest scoring thresholds (price for a
  3%+ adjusted discount, one more down close or the extra decline needed for the
  short-term reversal, the vol index's p90 level, VIX-curve inversion, credit OAS 5%,
  Fear & Greed 25). All rows push the score up; nothing can push it down anymore.

`docs/feed.xml` is a combined Atom feed for all six assets that only emits an entry
when an asset's verdict band changes from the prior day — subscribe via the "Alerts
(RSS)" link on any page.

## Development

```bash
python3 scripts/build_data.py                  # regenerate all six data-*.json files (stdlib only)
python3 -m unittest discover -s scripts        # scoring invariants + checks on the built files
python3 scripts/validate_stock_bands.py NVDA   # re-run a ticker's band validation
python3 -m http.server -d docs                 # view locally at http://localhost:8000
```

`scripts/test_rules.py` is the guard that rules v4 lacked: it asserts that no ticker
scores a band outside its validated set, that every scored band carries the evidence
string its card prints and every rejected band carries its reason, that the gauge axis
and `weights_max` describe reachable scores, and that the committed JSON and history
files agree with the rules that produced them. The workflow runs it after the build and
before the commit, so a bad ruleset fails CI instead of going live. Run against the v4
configuration it fails on six counts, including NVDA and GOOG carrying the falling-knife
band.

`scripts/test_fetchers.py` covers the external-data contracts offline. Guarded fetches
degrade to a note on the page, so a broken source looks exactly like a quiet one - which
is how the earnings handshake stayed dead for weeks. The tests stub curl and assert the
shape of each request, including that the `fc.yahoo.com` cookie hop tolerates its own
404 while every other hop still fails loudly.

Pages serves from the `docs/` folder on `main`. The workflow in
`.github/workflows/update-data.yml` refreshes all six data files each weekday at 13:35
and 19:15 UTC, and also on any push to `main` that touches `scripts/` - a rules change
otherwise lands with the pages describing rules the published JSON hasn't been rebuilt
under yet.

Not financial advice. Built as a personal decision aid.
