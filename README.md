# Should I buy GPIX today?

A tiny GitHub Pages tool that answers one question every weekday: is today a
better-or-worse-than-average day to buy [GPIX](https://am.gs.com/en-us/advisors/funds/detail/PV109746/38151J286/goldman-sachs-s-p-500-core-premium-income-etf)
(Goldman Sachs S&P 500 Premium Income ETF)?

A sister page (`gpiq.html`) answers the same question for GPIQ, the Nasdaq-100 version
of the fund. It swaps in Nasdaq-specific inputs - VXN instead of VIX, QQQ instead of
SPY - and displays one extra check of its own: the **tech fear premium** (VXN/VIX ratio
ranked against its own past year). That check is context-only: the audit found its
intuitive scoring backwards, so it shows but never scores.

Two single-stock pages (`tsla.html`, `spcx.html`) extend the same machinery to Tesla
and SpaceX with an asset-aware engine - see "Single-stock pages" below. TSLA scores
only what a dedicated 16-year validation pass proved (including the project's one
evidence-backed *negative* zone); SPCX (IPO June 2026) is an honest context-only page
that pins at 50 until enough history accumulates to test anything. Two more stock pages
(`nvda.html`, `goog.html`) sit adjacent to SPCX and reuse the TSLA-validated single-stock
framework provisionally for Nvidia and Alphabet (Class C), with on-page copy that says so.

The pipeline refreshes the data **three times each weekday**, in New York time: before
the open (07:00-09:15), in the last hour before the close (15:10-15:45) and after the
close (16:25-19:00). See "How the daily refresh works" below. Each run decides for
itself which price bars are finished, so the numbers are right whenever it runs. Fund JSONs also carry a `distributions` block (last 24 payouts, TTM sum,
and an estimated next ex-date projected from the payout cadence) which feeds the
"What it actually pays" bar chart on the GPIX/GPIQ pages. Both funds have gone ex-dividend on the
first New York Stock Exchange business day of every month for the last 21 months, so the script
projects that rule forward with a built-in NYSE holiday calendar (`nyse_holidays`,
`first_business_day`) and writes `distributions.upcoming`: the next three
`{ex, last_buy}` pairs, where `last_buy` is the business day before the ex-date (the last day to
own the shares in time). If fewer than 11 of the last 12 ex-dates follow that rule, it falls back to
the median gap between payouts and gives one entry. The pages tolerate a data file without
`upcoming` (they use the previous weekday of `next_ex_estimate`). Those pages print the last day to
buy with the closing bell in New York time and in Korea time (`Intl`, so summer and winter time are
right).

A fifth page (`retire.html`) answers "when can I retire?" for a monthly savings plan ($1,000 a
month into 80% GPIX / 20% GPIQ by default). "Retire" means the month when the monthly dividends
alone, after 15% US tax, pay the monthly goal you type in. Your job pay is not part of the math.
The page reads the funds' latest prices, distribution history and USD/KRW rate from
`data.json` / `data-gpiq.json`. The **Already invested** section accepts each fund's actual
share count and average purchase cost. It calculates total purchase cost, market value,
unrealized gain or loss, after-tax yield on purchase cost, the latest monthly distribution
estimate and trailing 12-month income. Distributions depend on shares, not purchase cost.
The forecast uses the trailing distribution yield, calculated directly from per-share payouts.
Existing fractional shares are preserved even when future purchases use whole shares.
Cash to invest is entered separately. The page stores personal inputs only in browser local
storage, with no server upload and no personal holdings in the public starting values.
Old `retire-settings-v2` dollar balances convert to estimated shares once. A notice asks users
to verify those shares and enter purchase costs, which the old page did not record.

Tax defaults to **15% US withholding** for a Korean resident with treaty eligibility. Users
can select 15.4% or enter a custom effective rate, including decimal rates. Each after-tax
figure deducts the selected rate once. The page links to the US–Korea treaty and a Korean
brokerage's distribution-reclassification notice. It distinguishes withholding estimates from
final tax after reclassification and annual Korean financial-income aggregation.

The month-by-month simulation tracks the two funds separately, reinvests after-tax income,
buys whole shares by default, retains unspent cash and adjusts the goal for inflation.
It reports the goal date, milestones, sources of portfolio growth, sensitivity to assumptions,
share-price scenarios and Korea's ₩20M financial-income check. That check covers only the two
funds in this model, not the user's other financial income or final tax liability.
Market snapshots refresh through the existing workflow three times each weekday. The browser
checks for updates every five minutes while visible, on return to the page and on a manual
refresh. Refreshes preserve purchase records. Missing distributions show “Unavailable”. Failed
refreshes retain the previous snapshot, and old or incomplete snapshots produce a visible notice.
Pure client-side vanilla JavaScript. No additional data provider, workflow or API key is required.

Run the holdings and simulation checks with `node --test scripts/test_retire.cjs`.

Three more blocks on the retire page, all driven by what you hold today:
**Your next dividend** shows the last day to buy before the next ex-dividend date, the closing bell
in New York and in Korea time, a live countdown (it moves to the next month when the bell has
rung), and what each fund would pay on the shares you hold after tax.
**What your dividends pay for** is an editable list of monthly costs (default: Claude $20, ChatGPT
$20 and lunch $20 four times a week, saved in local storage); it sorts them from cheap to
expensive and shows the month when the after-tax dividends cover each one and all of them.
**Your badges** are Apple-Watch-style medals for dividend levels ($10 to $1,000 a month, with your
goal in gold) and for each cost that gets paid; locked medals show a progress ring and the unlock
month, unlocked ones shine, tilt toward the pointer and pop with sparkles the moment you unlock one.
Medals are SVG drawn in the page script; drag one sideways and it spins (a spring settles it on
the front), and its back shows the unlock month. Everything respects `prefers-reduced-motion` and `?motion=off`.
**Days earned back** lets you set how many days a week you bring a lunchbox instead of buying the
weekly cost in your list (default Lunch, $20, 4 a week; a home lunch costs $6 by default). The
difference is added to the monthly savings, and the page shows how many days sooner you retire
(`fracMonths` adds the part of the last month, so small changes show in days). The **Your next
dividend** card also counts up what your shares have earned so far today and this month. The hero
shows how far your date moved since your last visit (a small record in local storage), with a
confetti burst when it moves sooner. All of this is client-side.

The GPIX page (`index.html`) also carries a Korean after-tax dividend calculator
("GPIX 배당 세후 계산기") under the verdict. Enter a USD amount, or tap the $10 / $100 / $300 /
$1,000 buttons, to see shares held (whole shares by default; the switch turns on fractional
buying), monthly and yearly dividends after 15% US withholding, and the pre- and after-tax
yield. Dollars are shown large and won small. The share price and last payout fill in from
`data.json` (each can be reset after editing). The USD/KRW rate comes from the `fx` block in
`data.json` (Yahoo's `KRW=X` quote, fetched by `scripts/build_data.py` with the rest of the daily data;
if that fetch fails the field is null and the page falls back to a typed-in rate). Pure
client-side vanilla JS with no libraries; figures count up with `requestAnimationFrame`, and all
motion is switched off under `prefers-reduced-motion`.

## How it decides

A GitHub Action runs on weekdays, pulls data from Yahoo Finance, FRED, CNN's
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
`rules_version` 4 with per-day `score` (W) and `score100`.

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
accordingly, and its report card carries a fourth "caution" row.

SPCX (Space Exploration Technologies Corp., first traded 2026-06-12) has ~2 months of
history: no 52-week high, no 200-day average, no 1-year percentile, no validation
sample. Nothing scores; the score pins at 50 with on-page copy saying that's a
statement about the tool's knowledge, not about SpaceX. Cards render honest "too
young" notes with the dates each measure unlocks (50d SMA ~Aug 2026, 200d ~Mar 2027,
1-year signals Jun 2027). Both stock pages also carry a single-stock honesty block:
the "any day is fine" floor was validated on diversified indexes and does not protect
against company-specific impairment - position sizing beats timing for single names.

NVDA and GOOG reuse the same stock engine, meter (40–73), and weights as TSLA
(`evidence: "tsla_framework"`). They score and backfill from Oct 2023 like the other
mature assets, but every scored card and the verdict summary state that the thresholds
come from the TSLA pass and are provisional pending a dedicated audit. That keeps the
Pages tabs useful without pretending Nvidia or Alphabet got their own era-robustness
study.

Stock pages add **earnings dates** to the event calendar (Yahoo quoteSummary
`calendarEvents` via its cookie+crumb handshake - keyless, guarded, omitted with a
note if the endpoint breaks). Earnings within 5 days flags as the dominant
single-stock volatility event.

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
python3 scripts/build_data.py   # regenerates all six data-*.json files (stdlib only)
python3 -m http.server -d docs  # view locally at http://localhost:8000
```

Pages serves from the `docs/` folder on `main`. The workflow in
`.github/workflows/update-data.yml` refreshes all six data files on weekdays.

## How the daily refresh works

**Why the schedule looks odd.** GitHub starts scheduled runs late. Until 2026-08-25 the delay
was under an hour. Since then it has been 2 to 9.5 hours, and GitHub gives no upper limit.
A plain cron at 15:15 New York time therefore produced a run after the close. So the
`schedule:` entries in the workflow fire *early on purpose*, and `scripts/gate.py` then waits
(at most 5.5 hours) for one of three New York windows. It works in both EDT and EST:

| Window | New York time | What it is for |
| --- | --- | --- |
| `pre-open` | 07:00-09:15 | Fresh page before the market opens (uses the last close). |
| `pre-close` | 15:10-15:45 | Provisional "buy at the close" reading from the live price. |
| `post-close` | 16:25-19:00 | Final numbers for the day. |

Each cron entry has a primary window (`PRIMARY` in `gate.py`). A run that starts after its
window falls forward to the next one. A window that already has a refresh (a commit tagged
`[pre-open]`, `[pre-close]` or `[post-close]` in the last 14 hours) is skipped. A run started by hand
(`workflow_dispatch`, also from outside GitHub) skips the gate and builds at once.
The build job runs one at a time (`concurrency:`), checks out the tip of `main`, and if a
push is rejected it rebuilds on the latest `main` instead of repeating the same push.

**Unfinished price bars.** While the US session is open, Yahoo's daily series ends with
today's bar, and its "close" is only the latest trade. The validated signals are
close-to-close effects, so `build_data.py` drops a bar that is still forming. The data then
describe the last completed session (`health.session.bar = "prior_close"`). In the last hour
before the close the live bar is kept instead and every output built from it is provisional
(`"provisional"`): the history row carries `"final": false`, the pages say "Live reading, not a
close yet", the report card ignores the row, and the alerts feed never announces it. The first run
after the close replaces it with the final value (`"final"`).

**Self-healing history.** On every run the newest 5 completed sessions are scored again (Yahoo
posts an ex-dividend payout late and re-bases all adjusted closes), and any stored live row whose
price differs from the final close is rebuilt from final data. The report card measures every
window on one adjusted-close basis.

**Nothing fails silently.** Every source records an outcome in the `health` block of each
`data*.json` (per-source `ok`, `as_of`, and whether the source is `core`, meaning it feeds the
score). Failures print `::warning::` lines in the run log. After the data are committed,
`scripts/check_health.py` turns the run red when a core source failed or the newest price bar is
more than 4 days old, so GitHub emails the owner. The pages show a red banner when the data are
more than 30 weekday hours old or a core input failed. The hand-typed FOMC and CPI date lists warn
30 days before they run out (the CPI list ends 2026-12-10).

Local checks: `python3 scripts/build_data.py` (needs network; on macOS Python you may have to set
`SSL_CERT_FILE=/etc/ssl/cert.pem`), `python3 scripts/check_health.py`, and
`GATE_NOW=2026-10-05T15:20:00-04:00 GITHUB_EVENT_NAME=schedule CRON="52 14 * * 1-5" GATE_NO_SLEEP=1 python3 scripts/gate.py`.

Not financial advice. Built as a personal decision aid.
