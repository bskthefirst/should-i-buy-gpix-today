#!/usr/bin/env python3
"""Build docs/data.json (GPIX) and docs/data-gpiq.json (GPIQ).

Fetches fund/underlying/vol-index history from Yahoo Finance's public
chart API, credit spreads and T-bill rates from FRED, CNN's Fear & Greed
index, and macro headlines from Google News RSS. Scores today's entry
conditions with transparent rules and backtests dip-waiting vs plain DCA
so each page can show whether timing has actually helped.

Rules v2 (post-audit): two independent long-history backtests of every
scoring band agreed that only a handful of rare conditions carry an
era-robust positive edge. Only those score now (all +1, composite 0..5):
vol index >= p90 of its trailing year, a 3%+ discount from the 52-week
high measured on ADJUSTED (total-return) closes, VIX term-structure
inversion, credit OAS >= 5%, and Fear & Greed <= 25. Every band the
audits found scored-backwards or noise is now permanently 0 - those
signals stay on the page as context. Negative scores are gone entirely:
the audits showed the tool couldn't spot bad days, so it stopped claiming
to.

Rules v3 adds the one new scored signal that survived a literature-mining
project (393 papers, ~40 candidate rules backtested on SPY 1993-2026 /
QQQ 1999-2026 with era-robustness and effective-N t-stat requirements):
short-term index reversal. When the fund's UNDERLYING proxy (SPY/QQQ) has
closed down 3+ consecutive sessions or fallen 3%+ over 5 sessions on
adjusted closes, the next day has been reliably above average (SPY
+0.21%/day excess, t=3.6; QQQ +0.39%, t=4.0; positive in every era).
Composite is now 0..6. The same project validated the overnight-premium
execution note shown on both pages (not scored).

Rules v4 keeps the same six validated conditions but weights each by the
size of the edge the validation work actually measured, instead of a flat
+1: the short-term reversal (the strongest, most era-robust effect)
carries 2.0, the adjusted discount and the vol-percentile extreme carry
1.5, and the three rarer/principled extremes carry 1.0. The weighted
composite W runs 0..8 and maps to a headline 0-100 buy score:
score100 = round(50 + W * 6.25). 50 means "typical day - buy on
schedule"; the score never goes below 50 by design, because two audits
found no reliable negative signal, so the tool doesn't claim any.

Rules v5 changes nothing about the funds and everything about how the
single-stock pages earn their weights. v4 shipped NVDA and GOOG on the
weights TSLA's validation pass produced, on the assumption that a
single-stock framework transfers between single stocks. Running the same
audit-grade test on their own histories (scripts/validate_stock_bands.py,
2016-2026) showed it does not: of TSLA's five bands, only two survive on
NVDA and only one on GOOG, and three of them - top-decile realized vol,
at-the-high momentum and the 10-20% "falling knife" - come out with the
WRONG SIGN on the new tickers. The falling knife is the worst offender:
it is the project's only negative band, and on NVDA and GOOG those days
were ABOVE baseline, so v4 was docking points for a good setup. Each
ticker now carries its own `bands` tuple and only those bands score;
rejected bands still render, annotated with the numbers that rejected
them. NVDA and GOOG consequently have no negative band and floor at 50,
like the funds.

GPIX is scored against the S&P 500 and the VIX; GPIQ against the
Nasdaq-100 (QQQ) and the VXN. GPIQ also displays the VXN/VIX "tech fear
premium" - the audit found its intuitive scoring backwards, so it no
longer scores.

Every signal also carries a "lean" ("good" | "bad" | "neutral"): a
purely descriptive, presentation-only read of current conditions so the
page never shows a vague tag. Leans never touch the score, the history
backfill, or the feed - those are score-based only.

Every non-core fetch is individually guarded: if a source is down, its
signal is emitted with score 0 and a "skipped" note instead of failing
the build.

Stdlib only - no dependencies - so it runs anywhere including GitHub Actions.
"""

from __future__ import annotations

import bisect
import json
import math
import re
import statistics
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
SITE = "https://bskthefirst.github.io/should-i-buy-gpix-today/"
UA = {"User-Agent": "Mozilla/5.0 (gpix-timing tool; personal use)"}
# CNN's endpoint returns HTTP 418 without a browser UA AND a cnn.com referer.
CNN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cnn.com/",
}

FUNDS = [
    {
        "key": "gpix",
        "ticker": "GPIX",
        "out": "data.json",
        "history_out": "history.json",
        "page": "",
        "underlying_symbol": "SPY",
        "underlying_name": "S&P 500",
        "vol_symbol": "%5EVIX",
        "vol_name": "VIX",
        "tech_premium": False,
        # GPIX trails ~8%: beating cash by 5.5+ pts is genuinely wide.
        "payout_bands": (5.5, 3.0),
        "news_query": 'federal+reserve+OR+inflation+OR+%22stock+market%22',
        # v4 meter axis: the headline 0-100 buy score, floored at 50
        "meter": (50, 100),
        # Validation stats for the short-term reversal card (next-day
        # excess return after the signal fires, on the underlying).
        "reversal_stats": "+0.21% next-day excess (t=3.6)",
    },
    {
        "key": "gpiq",
        "ticker": "GPIQ",
        "out": "data-gpiq.json",
        "history_out": "history-gpiq.json",
        "page": "gpiq.html",
        "underlying_symbol": "QQQ",
        "underlying_name": "Nasdaq-100",
        "vol_symbol": "%5EVXN",
        "vol_name": "VXN",
        "tech_premium": True,
        # GPIQ trails ~10%: demand a wider margin over cash before calling
        # it a tailwind, so the page doesn't award a permanent free point.
        "payout_bands": (7.5, 4.5),
        "news_query": 'Nasdaq+OR+%22tech+stocks%22+OR+%22Federal+Reserve%22+OR+AI',
        # v4 meter axis: the headline 0-100 buy score, floored at 50
        "meter": (50, 100),
        "reversal_stats": "+0.39% next-day excess (t=4.0)",
    },
    {
        # Single stock, NOT a covered-call ETF: its own validation pass
        # (TSLA 2010-2026, 4054 sessions, /tmp/tsla_validation) decides
        # what scores. Index-validated bands do NOT transfer blindly -
        # e.g. the 3%+ discount band tested as noise on TSLA, while
        # at-the-high tested as MOMENTUM (positive), the opposite of its
        # index behavior.
        "key": "tsla",
        "kind": "stock",
        "ticker": "TSLA",
        "asset_name": "Tesla",
        "out": "data-tsla.json",
        "history_out": "history-tsla.json",
        "page": "tsla.html",
        # Bands this ticker's own validation pass proved. Nothing outside
        # this tuple can move the score; see scripts/validate_stock_bands.py.
        "bands": ("crash", "down3", "rvol_p90", "at_high", "knife"),
        "range": "5y",  # 2y lead-in before the Oct 2023 history start
        "history_start": "2023-10-27",  # same window as GPIX/GPIQ report cards
        "news_query": "Tesla+OR+TSLA+stock+OR+%22Elon+Musk%22",
        "band_stats": {
            "crash": "+0.87pt next-day excess (t=2.2) and +11.2pt over the next quarter, positive in every era at every horizon",
            "down3": "+0.35pt next-day excess (t=1.7), positive in all four eras at the 1-day horizon (longer horizons mixed, hence half the crash weight)",
            "rvol_p90": "+0.35pt next-day excess (t=1.7) and +15.5pt over the next quarter, positive in every era",
            "at_high": "+0.62pt next-day excess (t=2.05), positive in every era",
            "knife": "negative in every era at both 1 day (t=-2.6) and 21 days (t=-1.9)",
        },
        "band_failed": {},
    },
    {
        # SpaceX IPO'd 2026-06-12: ~2 months of data. NOTHING can be
        # validated yet, so nothing scores - the page is context-only and
        # pins at 50 until enough history accumulates to test (mid-2027
        # for 1-year percentile signals).
        "key": "spcx",
        "kind": "stock",
        "ticker": "SPCX",
        "asset_name": "SpaceX",
        "out": "data-spcx.json",
        "history_out": "history-spcx.json",
        "page": "spcx.html",
        "bands": (),  # nothing validated, nothing scores
        "range": "1y",
        "history_start": None,  # from IPO
        "ipo": "2026-06-12",
        "news_query": "SpaceX+OR+Starlink+OR+%22Space+Exploration+Technologies%22",
        "band_stats": {},
        "band_failed": {},
    },
    {
        # Mature mega-cap on the same engine as TSLA - but the TSLA weights
        # were NOT assumed to transfer. NVDA got its own pass
        # (scripts/validate_stock_bands.py, 2016-2026, 2513 sessions) and
        # only two of TSLA's five bands survived it: the pullback family.
        # Top-decile realized vol, at-the-high momentum and the falling-knife
        # drawdown all failed on NVDA's own history - the first two are noise
        # here and the knife is outright wrong-signed - so they render as
        # context. NVDA therefore has no negative band and floors at 50.
        "key": "nvda",
        "kind": "stock",
        "ticker": "NVDA",
        "asset_name": "Nvidia",
        "out": "data-nvda.json",
        "history_out": "history-nvda.json",
        "page": "nvda.html",
        "bands": ("crash", "down3"),
        "range": "5y",
        "history_start": "2023-10-27",
        "news_query": "Nvidia+OR+NVDA+OR+GPU+OR+%22artificial+intelligence%22",
        "band_stats": {
            "crash": "+1.30pt next-day excess (t=2.0), positive in all four eras",
            "down3": "+0.76pt next-day excess (t=2.6), positive in all four eras",
        },
        "band_failed": {
            "rvol_p90": "On NVDA's own 10-year history the TSLA sign inverts: top-decile "
                        "realized-vol days ran −0.12pt next-day and −6.0pt over the next "
                        "month, below baseline in every era at the monthly horizon.",
            "at_high": "On NVDA, at-the-high days were flat next-day (+0.04pt) and split "
                       "between eras - nothing like TSLA's +0.62pt momentum edge.",
            "knife": "The 10–20% drawdown band is not a falling knife on NVDA: those days "
                     "ran +0.19pt next-day and +1.3pt over the next month, ABOVE baseline. "
                     "TSLA's one negative band does not transfer here.",
        },
    },
    {
        # Alphabet Class C. Same treatment as NVDA: its own validation pass,
        # and only the plain 3-down-close reversal survived it. The -12%
        # crash band is untestable on GOOG (10 occurrences in 10 years - the
        # threshold is calibrated to TSLA's ~2x higher 5-session sigma), and
        # the vol-scaled equivalent was era-mixed, so neither scores.
        "key": "goog",
        "kind": "stock",
        "ticker": "GOOG",
        "asset_name": "Alphabet",
        "out": "data-goog.json",
        "history_out": "history-goog.json",
        "page": "goog.html",
        "bands": ("down3",),
        "range": "5y",
        "history_start": "2023-10-27",
        "news_query": "Alphabet+OR+Google+OR+GOOG+OR+%22search+ads%22+OR+YouTube",
        "band_stats": {
            "down3": "+0.23pt next-day excess (t=1.8), positive in all four eras",
        },
        "band_failed": {
            "crash": "A 12% five-session drop has happened 10 times in GOOG's last decade - "
                     "too rare to test. The threshold is calibrated to TSLA, whose "
                     "5-session swings are more than twice as wide; the vol-equivalent "
                     "−5.5% band was era-mixed, so neither scores here.",
            "rvol_p90": "On GOOG the TSLA sign inverts: top-decile realized-vol days ran "
                        "−0.19pt next-day, below baseline in every era.",
            "at_high": "On GOOG, at-the-high days ran −0.07pt next-day and −2.2pt over the "
                       "next month, below baseline in all four eras - the opposite of "
                       "TSLA's momentum result.",
            "knife": "The 10–20% drawdown band is not a falling knife on GOOG: those days "
                     "ran +0.01pt next-day and +1.3pt over the next month, at or above "
                     "baseline. TSLA's one negative band does not transfer here.",
        },
    },
]

# Remaining FOMC decision days (second day of each meeting), per the
# Federal Reserve's published schedule.
FOMC_DATES = [
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
    "2027-01-27",
    "2027-03-17",
    "2027-04-28",
    "2027-06-09",
    "2027-07-28",
    "2027-09-15",
    "2027-10-27",
    "2027-12-08",
]

# BLS CPI release dates. The 2027 schedule is published late 2026.
CPI_DATES = [
    "2026-08-12",
    "2026-09-11",
    "2026-10-14",
    "2026-11-10",
    "2026-12-10",
]

WEEKLY_DCA_AMOUNT = 100.0
DIP_THRESHOLD = 0.03  # dip-waiter buys only >=3% below the high so far

SKIPPED_NOTE = "Data source unavailable today - signal skipped."

# Report-card horizon: ~1 trading month.
FWD_DAYS = 21

# Sessions of lead-in a day needs before any trailing-year measure is real:
# 251 for the 52-week window plus the 20-day realized-vol lookback.
WARMUP_SESSIONS = 271

# Bump this whenever the scoring rules change: history files carry it, and
# rows built under an older ruleset are discarded and rebuilt from scratch.
RULES_VERSION = 5

# Short verdict-band labels (feed titles, report card, timeline tooltip).
# "caution" exists only for validated single stocks: TSLA's 10-20%
# drawdown band tested negative in every era, the first (and only)
# evidence-backed worse-than-average zone in the system.
TONE_SHORT = {
    "good": "Better-than-usual entry",
    "ok": "Mild tailwind",
    "neutral": "No edge either way",
    "caution": "Worse-than-average zone",
}

# Buy-score (0-100) ranges of each band, for report-card labels. W>=1 maps
# to score100 56.25 and W>=3 to 68.75; the rendered boundaries round to
# 56 and 69. W<=-1 (stocks only) maps to <=43.75, rendered as <=44.
BAND_RANGE100 = {
    "good": "score ≥ 69",
    "ok": "score 56–68",
    "neutral": "score ~50",
    "caution": "score ≤ 44",
}

# ---------------------------------------------------------------------------
# Rules v4 weights - sized from the effect sizes the validation work
# actually measured, not vibes:
#   reversal  2.0  strongest validated edge: SPY +0.21%/1d excess (t=3.6),
#                  QQQ +0.39%/1d (t=4.0), era-robust in every period tested
#   discount  1.5  3%+ adjusted discount: +0.7-0.9pt/21d, era-stable on
#                  both indexes
#   vol p90   1.5  vol index >= p90 of trailing year: +1.0pt/21d on SPY,
#                  positive in every era
#   term inv  1.0  VIX/VIX3M >= 1.00: mixed evidence (one audit positive,
#                  one found era-flips) but rare and panic-coincident
#   credit    1.0  OAS >= 5%: principled, untestable in the available
#                  sample (spreads never got that wide)
#   F&G <=25  1.0  principled contrarian extreme, only ~1y testable history
# Weighted composite W ranges 0..8 (= WEIGHTS_MAX).
# ---------------------------------------------------------------------------
W_REVERSAL = 2.0
W_DISCOUNT = 1.5
W_VOL = 1.5
W_TERM = 1.0
W_CREDIT = 1.0
W_FG = 1.0
WEIGHTS_MAX = W_REVERSAL + W_DISCOUNT + W_VOL + W_TERM + W_CREDIT + W_FG  # 8.0


# ---------------------------------------------------------------------------
# Single-stock weights. The WEIGHTS are TSLA's; which of them apply to a given
# ticker is decided per ticker by that ticker's own pass
# (scripts/validate_stock_bands.py) and listed in its FUNDS["bands"] tuple.
# A band absent from that tuple can never move the score, no matter what the
# market does - it renders as context with the reason it failed.
#
# Sized from the dedicated TSLA validation pass (2010-2026, 4054 sessions,
# same forward-excess-return / era-split / effective-N method as the fund
# audits; artifacts in /tmp/tsla_validation):
#   crash reversal  2.0  5d return <= -12% (vol-scaled for TSLA's ~3x index
#                        sigma): +0.87pt next-day excess (t=2.2), +11.2pt
#                        over the next quarter, positive in EVERY era at
#                        EVERY horizon tested. Fires ~11 days/yr.
#   3 down closes   1.0  +0.35pt next-day excess (t=1.7), positive in all
#                        four eras at the 1-day horizon (longer horizons
#                        mixed, so it earns half the crash weight).
#   realized vol    1.5  20d realized vol >= p90 of its trailing year:
#                        +0.35pt/1d (t=1.7) and +15.5pt/63d, each positive
#                        in every era. (No public TSLA IV index exists
#                        keyless; realized vol is the substitute.)
#   at 52w high     1.0  within 0.5% of the high: +0.62pt/1d (t=2.05),
#                        positive in all eras - single-name MOMENTUM, the
#                        opposite of what at-the-high meant for indexes.
#   falling knife  -1.5  drawdown 10-20% from the 52w high: NEGATIVE in
#                        every era at both 1d (t=-2.6) and 21d (t=-1.9)
#                        horizons. The first evidence-backed "worse than
#                        average to buy" zone in this whole project - it
#                        exists for a single name even though two audits
#                        found none for diversified indexes.
# Failed the bar (context-only): the index 3-7% discount band (does not
# transfer), drawdown >=35% "deep value" (+13pt/63d but t=1.1, narrowly
# missed), market-wide VIX conditions applied to TSLA (era-flipped).
# ---------------------------------------------------------------------------
WS_CRASH = 2.0
WS_DOWN3 = 1.0
WS_RVOL = 1.5
WS_HIGH = 1.0
WS_KNIFE = -1.5


def score_stock_pullback(down_streak: int, ret5: float | None, bands: tuple) -> float:
    """Crash reversal (5d <= -12%) at 2.0, plain 3-down-close streak at 1.0.
    The crash band supersedes the streak when both fire."""
    if "crash" in bands and ret5 is not None and ret5 <= -12.0:
        return WS_CRASH
    if "down3" in bands and down_streak >= 3:
        return WS_DOWN3
    return 0.0


def score_stock_rvol(pct: float | None, bands: tuple) -> float:
    """Realized vol >= p90 of its own trailing year."""
    return WS_RVOL if "rvol_p90" in bands and pct is not None and pct >= 90 else 0.0


def score_stock_high_or_knife(dd: float, bands: tuple) -> float:
    """Distance from the 52-week high, single-name edition: at the high is
    momentum (+1.0), the 10-20% band is the falling knife (-1.5)."""
    if "at_high" in bands and dd <= 0.5:
        return WS_HIGH
    if "knife" in bands and 10.0 <= dd < 20.0:
        return WS_KNIFE
    return 0.0


def stock_weight_range(bands: tuple) -> tuple[float, float]:
    """(lowest, highest) weighted composite W a band set can actually reach.

    Not a naive sum of the positive weights: a 12% five-session drop leaves the
    price at least 12% under its 52-week high, so the crash and at-the-high
    bands are mutually exclusive. TSLA's arithmetic maximum of 4.5 is
    unreachable; its real ceiling is 3.5, and the gauge must not advertise a
    denominator nobody can hit.
    """
    rvol = WS_RVOL if "rvol_p90" in bands else 0.0
    crash = WS_CRASH if "crash" in bands else 0.0
    down3 = WS_DOWN3 if "down3" in bands else 0.0
    high = WS_HIGH if "at_high" in bands else 0.0
    knife = WS_KNIFE if "knife" in bands else 0.0
    return knife, max(crash + rvol, down3 + rvol + high)


def stock_meter(bands: tuple) -> tuple[int, int]:
    """Gauge axis covering exactly the reachable buy scores, plus a hair of
    margin so the needle never sits on the rim."""
    lo, hi = stock_weight_range(bands)
    if hi <= 0:  # nothing scores (SPCX): keep the fund pages' axis
        return (50, 100)
    return (50 if lo == 0 else score100_of(lo) - 1, score100_of(hi) + 1)


def stock_tones(bands: tuple) -> tuple:
    """Verdict bands a ticker can actually land in, in display order. A ticker
    with no negative band never shows "caution"; one that can't reach W=3 never
    shows "good", so the report card stops listing rows that can never fill."""
    lo, hi = stock_weight_range(bands)
    tones = []
    if hi >= 3:
        tones.append("good")
    if hi >= 1:
        tones.append("ok")
    tones.append("neutral")
    if lo <= -1:
        tones.append("caution")
    return tuple(tones)


def verdict_tone_stock(score: float) -> str:
    """Stock verdict bands add "caution" below W <= -1, reachable only where a
    ticker's own pass validated a negative band (TSLA's falling knife). On
    tickers without one the score cannot go below 50 in the first place."""
    if score >= 3:
        return "good"
    if score >= 1:
        return "ok"
    if score <= -1:
        return "caution"
    return "neutral"


def score100_of(w: float) -> int:
    """Headline 0-100 buy score: 50 on a typical day (W=0), 100 when every
    weighted condition fires (W=8). Floored at 50 by design: two audits
    found no reliable negative signal, so the tool doesn't claim any."""
    return round(50 + w * 6.25)


def fmt_w(w: float) -> str:
    """Weighted scores render as +2, +1.5, +3.5 - no trailing .0 noise."""
    return f"{w:+g}"


def viz(pos: float, labels: list[str], zones: list | None = None,
        marker: float | None = None) -> dict:
    """Per-signal visualization data for the pages' bullet bars: where
    today's value sits on a 0-100 track, which regions score (or hurt),
    and the threshold tick. Pure presentation - never touches scoring."""
    out: dict = {"pos": round(max(0.0, min(100.0, pos)), 1), "labels": labels}
    if zones:
        out["zones"] = zones
    if marker is not None:
        out["marker"] = marker
    return out


# ---------------------------------------------------------------------------
# Scoring rules (v4) - the single source of truth. The live build, the
# historical backfill and the "what would flip the verdict" panel all call
# these, so the thresholds can never drift apart. Only bands that
# independent backtests found era-robust score; everything else is 0.
# Each returns its evidence weight when the condition fires, else 0.
# ---------------------------------------------------------------------------

def score_vol_percentile(pct: float) -> float:
    """Vol index >= p90 of its trailing year: +1.03pt/21d on SPY,
    positive in every era tested. Lower bands showed no reliable edge."""
    return W_VOL if pct >= 90 else 0.0


def score_term_structure(ratio: float) -> float:
    """Inversion only. Evidence is mixed (one audit positive, one found
    era-flips) but it fires rarely and coincides with genuine panics."""
    return W_TERM if ratio >= 1.00 else 0.0


def score_drawdown(dd: float) -> float:
    """3%+ discount from the 52-week high on ADJUSTED closes: the one
    era-robust discount band (+0.7-0.9pt/21d on both indexes). The old
    >=7% "+2" was a dot-com artifact and the at-the-high "-1" didn't
    survive testing (at-high days still averaged +0.44%/21d)."""
    return W_DISCOUNT if dd >= 3 else 0.0


def score_credit(oas: float) -> float:
    """OAS >= 5%: principled but untested - spreads never got this wide in
    the testable sample. The widening "-1" is gone: its 34 test days
    rebounded +2.67pt."""
    return W_CREDIT if oas >= 5.0 else 0.0


def score_fear_greed(fg: float) -> float:
    """Contrarian extreme only (~1 year of testable history)."""
    return W_FG if fg <= 25 else 0.0


def reversal_inputs(adj: list[float], i: int) -> tuple[int, float | None]:
    """Reversal signal inputs at index i of an adjusted-close series:
    (consecutive-down-close streak ending at i, 5-session total return %).
    ret5 is None when fewer than 6 observations are available."""
    streak = 0
    j = i
    while j > 0 and adj[j] < adj[j - 1]:
        streak += 1
        j -= 1
    ret5 = (adj[i] / adj[i - 5] - 1) * 100 if i >= 5 else None
    return streak, ret5


def score_reversal(down_streak: int, ret5: float | None) -> float:
    """Short-term index reversal on the UNDERLYING proxy (SPY/QQQ):
    3+ consecutive down closes OR a 5-session return <= -3%, on adjusted
    closes. The one new rule that survived a 393-paper literature review
    plus independent revalidation (Park 1995; Chordia-Roll-Subrahmanyam
    2002): SPY +0.21%/next-day excess (t=3.6), QQQ +0.39% (t=4.0),
    positive in every era including 2016+. The strongest validated edge,
    hence the heaviest v4 weight."""
    if down_streak >= 3:
        return W_REVERSAL
    if ret5 is not None and ret5 <= -3.0:
        return W_REVERSAL
    return 0.0


def verdict_tone(score: float) -> str:
    """Verdict bands on the weighted composite W (0..8). Same numeric
    thresholds as v3 but on weighted points, so membership differs:
    W >= 3 (score100 >= 69, e.g. reversal + discount, or two mid-weight
    conditions) = good; W >= 1 (score100 >= 56, any single condition)
    = ok; W = 0 (score100 = 50) = neutral."""
    if score >= 3:
        return "good"
    if score >= 1:
        return "ok"
    return "neutral"


def fetch_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def curl_fetch(url: str, headers: dict | None = None) -> bytes:
    """FRED and CNN stall or reject Python's urllib (TLS/header
    fingerprinting) but serve curl instantly - so use curl for them."""
    cmd = ["curl", "-sS", "--fail", "-m", "30", url]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


@lru_cache(maxsize=None)
def fetch_earnings(ticker: str) -> tuple[str, bool] | None:
    """Next earnings date via Yahoo quoteSummary calendarEvents.

    The endpoint has required a cookie+crumb handshake since 2023, but both
    steps are keyless and serve curl fine. Returns (iso_date, is_estimate) or
    None; callers treat None as "omit with a note" - earnings display is a
    nice-to-have, never build-critical.

    The cookie-priming hop is the fiddly part: fc.yahoo.com answers 404 while
    still setting the session cookies the crumb endpoint requires. Running it
    under curl --fail turns that expected 404 into exit 22, which aborted the
    whole handshake and left every stock page permanently without an earnings
    date - so this one request must not use --fail.
    """
    import tempfile
    import urllib.parse

    moz = CNN_HEADERS["User-Agent"]
    with tempfile.NamedTemporaryFile() as jar:
        def crl(args, fail=True):
            return subprocess.run(
                ["curl", "-sS", "-m", "20", "-A", moz] + (["--fail"] if fail else []) + args,
                capture_output=True, check=True,
            ).stdout

        crl(["-c", jar.name, "-o", "/dev/null", "https://fc.yahoo.com"], fail=False)
        crumb = crl(["-b", jar.name, "https://query1.finance.yahoo.com/v1/test/getcrumb"]).decode().strip()
        if not crumb or "<" in crumb:
            return None
        data = json.loads(crl([
            "-b", jar.name,
            "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
            f"{ticker}?modules=calendarEvents&crumb={urllib.parse.quote(crumb)}",
        ]))
    earnings = data["quoteSummary"]["result"][0]["calendarEvents"]["earnings"]
    dates = earnings.get("earningsDate") or []
    today = date.today()
    upcoming = sorted(d["fmt"] for d in dates if "fmt" in d and d["fmt"] >= today.isoformat())
    if not upcoming:
        return None
    return upcoming[0], bool(earnings.get("isEarningsDateEstimate"))


def yahoo_chart(symbol: str, range_: str, events: bool = False) -> dict:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval=1d"
    )
    if events:
        url += "&events=div"
    return fetch_json(url)["chart"]["result"][0]


def chart_rows(result: dict) -> list[tuple[date, float, float]]:
    """(date, close, adjusted_close) rows. Adjusted close folds
    distributions back in, which is what total-return math needs."""
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose", closes)
    out = []
    for ts, close, aclose in zip(stamps, closes, adj):
        if close is None:
            continue
        out.append(
            (
                datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                round(close, 4),
                round(aclose if aclose is not None else close, 4),
            )
        )
    return out


@lru_cache(maxsize=None)
def cached_rows(symbol: str, range_: str) -> tuple:
    """Shared series (VIX, VIX3M, SPY, ...) are used by both fund builds;
    fetch each once per run."""
    return tuple(chart_rows(yahoo_chart(symbol, range_)))


def fetch_fred(series_id: str) -> list[tuple[date, float]]:
    """FRED CSV: header 'observation_date,SERIES_ID'; missing values are '.'.

    Fetched from well before the funds' Oct 2023 inception so the same rows
    serve both the live signals (which only use the tail) and the backfill.
    Note: fredgraph.csv caps BAMLH0A0HYM2 at the trailing ~3 years no matter
    what cosd asks for - still enough lead-in for the 1-month-change signal.
    """
    start = "2022-06-01"
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    text = curl_fetch(url).decode()
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) != 2:
            continue
        try:
            rows.append((date.fromisoformat(parts[0]), float(parts[1])))
        except ValueError:
            continue  # '.' or malformed
    if not rows:
        raise ValueError(f"no parseable rows for {series_id}")
    return rows


@lru_cache(maxsize=None)
def cached_fred(series_id: str) -> tuple:
    return tuple(fetch_fred(series_id))


@lru_cache(maxsize=None)
def cached_fg_json() -> dict:
    return json.loads(
        curl_fetch(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=CNN_HEADERS,
        )
    )


def cached_fear_greed() -> tuple[float, str]:
    data = cached_fg_json()
    return float(data["fear_and_greed"]["score"]), str(data["fear_and_greed"]["rating"])


@lru_cache(maxsize=None)
def fear_greed_history() -> tuple:
    """(date, score) series from CNN's own payload. Only reaches back ~1
    year, so backfilled rows before coverage score F&G as 0 (noted on page)."""
    pts = cached_fg_json()["fear_and_greed_historical"]["data"]
    rows = sorted(
        (datetime.fromtimestamp(p["x"] / 1000, tz=timezone.utc).date(), float(p["y"]))
        for p in pts
    )
    return tuple(rows)


def sma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def realized_vol_series(adj: list[float], window: int = 20) -> list[float | None]:
    """Annualized 20-day realized volatility (%), per index. The stock
    pages' substitute for an implied-vol index - no keyless single-stock
    IV series exists."""
    rv: list[float | None] = [None] * len(adj)
    for i in range(window, len(adj)):
        rets = [math.log(adj[j] / adj[j - 1]) for j in range(i - window + 1, i + 1)]
        rv[i] = statistics.stdev(rets) * math.sqrt(252) * 100
    return rv


def trailing_pct(series: list, i: int, min_obs: int = 252) -> float | None:
    """Percentile of series[i] within its trailing year (inclusive).
    None until a full year of observations exists - the min-history guard
    that keeps SPCX's too-young signals from pretending to know things."""
    if i < 0 or series[i] is None:
        return None
    window = [x for x in series[max(0, i - 251): i + 1] if x is not None]
    if len(window) < min_obs:
        return None
    return sum(1 for x in window if x < series[i]) / len(window) * 100


def fetch_headlines(query: str, limit: int = 6) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            root = ET.fromstring(resp.read())
    except Exception:
        return []
    items = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        try:
            pub_iso = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
        except Exception:
            pub_iso = None
        # Google News titles end with " - Source"
        source = ""
        m = re.search(r"\s-\s([^-]+)$", title)
        if m:
            source = m.group(1).strip()
            title = title[: m.start()].strip()
        items.append({"title": title, "source": source, "link": link, "published": pub_iso})
        if len(items) >= limit:
            break
    return items


def backtest(history: list[tuple[date, float]]) -> dict:
    """Weekly $100 DCA vs saving the same $100/week in cash and only
    buying when price is >=3% below the highest close seen so far.

    Runs on ADJUSTED closes, so monthly distributions count as reinvested
    for whoever holds shares. Cash waiting for a dip earns nothing - which
    is the real cost of waiting with a high-distributing fund.
    """
    dca_shares = 0.0
    dca_invested = 0.0
    dip_shares = 0.0
    dip_cash = 0.0
    dip_invested = 0.0
    dip_buy_days = 0
    high_so_far = 0.0
    last_week = None

    for day, close in history:
        high_so_far = max(high_so_far, close)
        week = day.isocalendar()[:2]
        if week != last_week:
            last_week = week
            dca_shares += WEEKLY_DCA_AMOUNT / close
            dca_invested += WEEKLY_DCA_AMOUNT
            dip_cash += WEEKLY_DCA_AMOUNT
            dip_invested += WEEKLY_DCA_AMOUNT
        if dip_cash > 0 and close <= high_so_far * (1 - DIP_THRESHOLD):
            dip_shares += dip_cash / close
            dip_cash = 0.0
            dip_buy_days += 1

    last_close = history[-1][1]
    dca_value = dca_shares * last_close
    dip_value = dip_shares * last_close + dip_cash
    return {
        "start": history[0][0].isoformat(),
        "end": history[-1][0].isoformat(),
        "weekly_amount": WEEKLY_DCA_AMOUNT,
        "dip_threshold_pct": DIP_THRESHOLD * 100,
        "invested": round(dca_invested, 2),
        "dca_value": round(dca_value, 2),
        "dca_return_pct": round((dca_value / dca_invested - 1) * 100, 2),
        "dip_value": round(dip_value, 2),
        "dip_return_pct": round((dip_value / dip_invested - 1) * 100, 2),
        "dip_cash_uninvested": round(dip_cash, 2),
        "dip_buy_days": dip_buy_days,
        "winner": "dca" if dca_value >= dip_value else "dip",
    }


def load_history(path: Path) -> list[dict]:
    """Rows are only trusted if the file was written under the current
    RULES_VERSION; older files (including the bare-list v1 format) are
    discarded so the backfill regenerates everything under today's rules."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if isinstance(data, dict) and data.get("rules_version") == RULES_VERSION:
        return data.get("rows", [])
    return []


def distributions_block(div_events: list, today: date) -> dict | None:
    """Payout history + estimated next ex-date for the fund pages.

    Yahoo's chart events give every historical ex-date and per-share amount;
    Goldman publishes no machine-readable calendar, so the NEXT ex-date is
    projected from the median gap between recent ex-dates and always flagged
    as an estimate. Returns None when there is nothing to show."""
    if not div_events:
        return None
    recent = div_events[-24:]
    est = None
    if len(recent) >= 3:
        gaps = sorted(
            (recent[i][0] - recent[i - 1][0]).days for i in range(1, len(recent))
        )
        med = max(1, gaps[len(gaps) // 2])
        nxt = recent[-1][0] + timedelta(days=med)
        while nxt < today:
            nxt += timedelta(days=med)
        est = nxt.isoformat()
    return {
        "history": [{"date": d.isoformat(), "amount": round(a, 4)} for d, a in recent],
        "ttm_sum": round(sum(a for d, a in div_events if d >= today - timedelta(days=365)), 4),
        "last_ex": recent[-1][0].isoformat(),
        "last_amount": round(recent[-1][1], 4),
        "next_ex_estimate": est,
        "is_estimate": True,
    }


def upsert_live_row(hist: list[dict], live_row: dict) -> bool:
    """Insert today's row, or replace it if a row for today already exists
    and differs. The workflow now runs twice a day: the afternoon run must
    update the morning verdict, not silently keep the stale one. Returns
    True when the history changed."""
    for i, r in enumerate(hist):
        if r["date"] == live_row["date"]:
            if r != live_row:
                hist[i] = live_row
                return True
            return False
    hist.append(live_row)
    return True


def save_history(path: Path, rows: list[dict]) -> None:
    rows.sort(key=lambda r: r["date"])
    body = ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows)
    path.write_text(
        '{"rules_version": %d, "rows": [\n' % RULES_VERSION + body + "\n]}\n"
    )


def _series_or_empty(fn) -> list:
    try:
        return list(fn())
    except Exception:
        return []


def build_history(fund: dict, fund_rows: list, live_row: dict) -> list[dict]:
    """Append today's live row and backfill any missing historical dates.

    Backfilled rows are scored with the same rules as the live build, using
    only data available as of each date (trailing percentiles, 52-week highs,
    TTM payouts all end at that date). Cheap after the first run: dates
    already in the file are skipped. Every context series is guarded - if one
    is unavailable its signal simply contributes 0, like a live skip.
    """
    path = DOCS / fund["history_out"]
    hist = load_history(path)
    have = {r["date"] for r in hist}

    # 4y ranges give daily bars with a full 1-year lead-in before the funds'
    # Oct 2023 inception (range=max would degrade to weekly bars). Only the
    # six v4 scoring inputs are needed: the context-only signals can never
    # move a historical score. The underlying's 4y range also covers the
    # reversal signal's 5-session lookback at the Oct 2023 backfill start.
    vol4 = _series_or_empty(lambda: cached_rows(fund["vol_symbol"], "4y"))
    vix4 = _series_or_empty(lambda: cached_rows("%5EVIX", "4y"))
    vix3m4 = _series_or_empty(lambda: cached_rows("%5EVIX3M", "4y"))
    und4 = _series_or_empty(lambda: cached_rows(fund["underlying_symbol"], "4y"))
    hy = _series_or_empty(lambda: cached_fred("BAMLH0A0HYM2"))
    fg = _series_or_empty(fear_greed_history)

    und_d = [r[0] for r in und4]; und_a = [r[2] for r in und4]
    vol_d = [r[0] for r in vol4]; vol_c = [r[1] for r in vol4]
    vix_d = [r[0] for r in vix4]; vix_c = [r[1] for r in vix4]
    v3_d = [r[0] for r in vix3m4]; v3_c = [r[1] for r in vix3m4]
    hy_d = [r[0] for r in hy]; hy_v = [r[1] for r in hy]
    fg_d = [r[0] for r in fg]; fg_v = [r[1] for r in fg]

    def asof(dates: list, d: date) -> int:
        """Index of the latest observation on/before d, or -1."""
        return bisect.bisect_right(dates, d) - 1

    # Drawdown is measured on ADJUSTED closes so distributions don't
    # masquerade as discounts (~0.8%/mo payouts add up fast).
    adj = [a for _, _, a in fund_rows]

    def scores_for(i: int) -> list[tuple[str, float]]:
        d = fund_rows[i][0]
        sc: list[tuple[str, float]] = []
        vi = asof(vol_d, d)
        if vi >= 0:
            win = vol_c[max(0, vi - 251): vi + 1]
            pct = sum(1 for c in win if c < vol_c[vi]) / len(win) * 100
            sc.append((f"{fund['vol_name']} vs its own past year", score_vol_percentile(pct)))
        a, b = asof(vix_d, d), asof(v3_d, d)
        if a >= 0 and b >= 0 and v3_c[b]:
            sc.append(("VIX term structure (1mo/3mo)", score_term_structure(vix_c[a] / v3_c[b])))
        hi52 = max(adj[max(0, i - 251): i + 1])
        dd = round((1 - adj[i] / hi52) * 100, 1)
        sc.append(("Discount from 52-week high", score_drawdown(dd)))
        ui = asof(und_d, d)
        if ui >= 0:
            streak, ret5 = reversal_inputs(und_a, ui)
            sc.append(("Short-term pullback (reversal)", score_reversal(streak, ret5)))
        hj = asof(hy_d, d)
        if hj >= 0:
            sc.append(("Credit stress (junk-bond spreads)", score_credit(hy_v[hj])))
        fj = asof(fg_d, d)
        if fj >= 0 and (d - fg_d[fj]).days <= 5:
            sc.append(("Fear & Greed index", score_fear_greed(fg_v[fj])))
        return sc

    changed = False
    # Everything except the last row (today) is reconstructed; today comes
    # from the live build so the recorded verdict matches the page exactly.
    for i in range(len(fund_rows) - 1):
        iso = fund_rows[i][0].isoformat()
        if iso in have:
            continue
        sc = scores_for(i)
        total = round(sum(s for _, s in sc), 2)
        drivers = [
            f"{fmt_w(s)} {n}"
            for n, s in sorted((x for x in sc if x[1]), key=lambda x: -abs(x[1]))[:3]
        ]
        hist.append({
            "date": iso,
            "score": total,
            "score100": score100_of(total),
            "tone": verdict_tone(total),
            "price": fund_rows[i][1],
            "adj": fund_rows[i][2],
            "backfilled": True,
            "drivers": drivers,
        })
        have.add(iso)
        changed = True
    changed = upsert_live_row(hist, live_row) or changed
    if changed or not path.exists():
        save_history(path, hist)
    else:
        hist.sort(key=lambda r: r["date"])
    return hist


def report_card(hist: list[dict], tones: tuple = ("good", "ok", "neutral")) -> dict:
    """Forward FWD_DAYS-trading-day total return (adjusted closes) of buys
    made under each verdict band, vs the all-days baseline."""
    fwd = []
    for i in range(len(hist) - FWD_DAYS):
        a, b = hist[i], hist[i + FWD_DAYS]
        if a.get("adj") and b.get("adj"):
            fwd.append((a["tone"], (b["adj"] / a["adj"] - 1) * 100))

    def agg(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0, "avg_pct": None, "median_pct": None}
        return {
            "n": len(vals),
            "avg_pct": round(statistics.fmean(vals), 2),
            "median_pct": round(statistics.median(vals), 2),
        }

    fg_start = None
    try:
        fg_start = fear_greed_history()[0][0].isoformat()
    except Exception:
        pass
    return {
        "horizon_days": FWD_DAYS,
        "start": hist[0]["date"],
        "end": hist[-1]["date"],
        "days_total": len(hist),
        "backfilled_days": sum(1 for r in hist if r.get("backfilled")),
        "fg_coverage_start": fg_start,
        "baseline": agg([v for _, v in fwd]),
        "bands": [
            dict(tone=t, label=f"{TONE_SHORT[t]} ({BAND_RANGE100[t]})", **agg([v for tt, v in fwd if tt == t]))
            for t in tones
        ],
    }


def write_feed(hist_by_key: dict[str, list[dict]]) -> int:
    """Atom feed with one entry per verdict-band change per fund (capped at
    the 50 most recent). Quiet by design - no entry on unchanged days."""
    events = []
    for fund in FUNDS:
        prev = None
        for r in hist_by_key.get(fund["key"]) or []:
            if prev is not None and r["tone"] != prev["tone"]:
                events.append({
                    "fund": fund,
                    "date": r["date"],
                    "frm": prev["tone"],
                    "to": r["tone"],
                    "score": r["score"],
                    "score100": r.get("score100", score100_of(r["score"])),
                    "drivers": r.get("drivers") or [],
                    "backfilled": r.get("backfilled", False),
                })
            prev = r
    events.sort(key=lambda e: e["date"], reverse=True)
    events = events[:50]

    ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", ns)
    feed = ET.Element(f"{{{ns}}}feed")

    def sub(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
        if text is not None:
            el.text = text
        return el

    sub(feed, "title", "Should I buy GPIX/GPIQ/TSLA/SPCX/NVDA/GOOG today? - verdict changes")
    sub(feed, "id", SITE + "feed.xml")
    sub(feed, "link", rel="self", href=SITE + "feed.xml")
    sub(feed, "link", rel="alternate", href=SITE)
    sub(feed, "updated", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sub(feed, "subtitle", "One entry per verdict-band change per fund - quiet by design.")
    author = sub(feed, "author")
    sub(author, "name", "should-i-buy-gpix-today")
    for e in events:
        t = e["fund"]["ticker"]
        entry = sub(feed, "entry")
        sub(entry, "title", f"{t}: {TONE_SHORT[e['frm']]} → {TONE_SHORT[e['to']]} (buy score {e['score100']}/100)")
        sub(entry, "link", rel="alternate", href=SITE + e["fund"]["page"])
        sub(entry, "id", f"tag:bskthefirst.github.io,2026:{e['fund']['key']}:{e['date']}")
        sub(entry, "updated", f"{e['date']}T13:35:00Z")
        body = (
            f"{t} verdict moved from \"{TONE_SHORT[e['frm']]}\" to "
            f"\"{TONE_SHORT[e['to']]}\" (buy score {e['score100']}/100, "
            f"weighted signals {fmt_w(e['score'])})."
        )
        if e["drivers"]:
            body += " Driving signals: " + "; ".join(e["drivers"]) + "."
        if e["backfilled"]:
            body += " (Reconstructed from backfilled history - today's rules applied to that day's data.)"
        sub(entry, "summary", body)
    xml = ET.tostring(feed, encoding="unicode")
    (DOCS / "feed.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n' + xml + "\n")
    return len(events)


def guarded(name: str, builder):
    """Run a signal builder; on any failure emit a neutral placeholder so
    one flaky endpoint never breaks the daily build."""
    try:
        return builder()
    except Exception:
        return {"name": name, "value": "n/a", "score": 0, "weight": 0, "lean": "neutral", "note": SKIPPED_NOTE}


def build(fund: dict) -> dict:
    ticker = fund["ticker"]
    und_name = fund["underlying_name"]
    vol_name = fund["vol_name"]

    # ---- core fetches (build fails loudly if these break) ----
    # Note: range=max silently degrades to weekly bars on Yahoo's API;
    # 3y keeps daily granularity and covers both funds' full life
    # (GPIX and GPIQ both launched Oct 2023).
    fund_result = yahoo_chart(ticker, "3y", events=True)
    fund_rows = chart_rows(fund_result)
    # 4y underlying: the same cached series serves the live signals (which
    # only use the tail) and the backfill, where the reversal signal needs
    # adjusted closes from before the funds' Oct 2023 inception.
    und = list(cached_rows(fund["underlying_symbol"], "4y"))
    vol = list(cached_rows(fund["vol_symbol"], "1y"))

    fund_closes = [c for _, c, _ in fund_rows]
    und_closes = [c for _, c, _ in und]
    vol_closes = [c for _, c, _ in vol]

    price = fund_closes[-1]
    day_change = (price / fund_closes[-2] - 1) * 100 if len(fund_closes) > 1 else 0.0
    sma50 = sma(fund_closes, 50)  # raw: overlaid on the raw price chart
    sma200 = sma(fund_closes, 200)  # raw, for the chart's second dashed line
    year = fund_closes[-252:] if len(fund_closes) >= 252 else fund_closes
    high52 = max(year)
    # Raw drawdown for the price-context line (people see raw prices in
    # their brokerage); the SIGNAL uses the adjusted version below.
    drawdown = round((1 - price / high52) * 100, 1)

    # Adjusted (total-return) series: distributions reinvested, so a ~0.8%/mo
    # payout doesn't masquerade as a discount. Signal math uses these.
    fund_adj = [a for _, _, a in fund_rows]
    adj_price = fund_adj[-1]
    year_adj = fund_adj[-252:] if len(fund_adj) >= 252 else fund_adj
    high52_adj = max(year_adj)
    drawdown_adj = round((1 - adj_price / high52_adj) * 100, 1)
    sma50_adj = sma(fund_adj, 50)
    vs_sma50_adj = round((adj_price / sma50_adj - 1) * 100, 1) if sma50_adj else 0.0

    und_price = und_closes[-1]
    und_sma200 = sma(und_closes, 200)
    und_above_200 = und_sma200 is not None and und_price >= und_sma200

    # Underlying adjusted closes for the short-term reversal signal
    # (validated on the index proxy, not the fund).
    und_adj = [a for _, _, a in und]
    und_streak, und_ret5 = reversal_inputs(und_adj, len(und_adj) - 1)

    vol_level = vol_closes[-1]

    today = date.today()

    # ---- income data (used by a signal, the backfill, and the page) ----
    ttm_yield = None
    tbill = None
    div_events: list[tuple[date, float]] = []
    try:
        divs = fund_result.get("events", {}).get("dividends", {})
        div_events = sorted(
            (datetime.fromtimestamp(d["date"], tz=timezone.utc).date(), d["amount"])
            for d in divs.values()
        )
        ttm_sum = sum(a for dd, a in div_events if dd >= today - timedelta(days=365))
        if ttm_sum > 0:
            ttm_yield = round(ttm_sum / price * 100, 2)
    except Exception:
        pass
    try:
        tbill = round(cached_fred("DGS3MO")[-1][1], 2)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Signals. Positive score = tilts toward buying today.
    # ------------------------------------------------------------------
    signals = []

    # 1. Vol index vs its own past year (percentile of trailing daily closes).
    # Only the top decile scores - the one vol band both audits found
    # era-robust (+1.03pt/21d on SPY), which is why it carries a 1.5 weight.
    def sig_vol_percentile():
        below = sum(1 for c in vol_closes if c < vol_level)
        pct = below / len(vol_closes) * 100
        s = score_vol_percentile(pct)
        if s:
            lean = "good"
            note = f"{und_name} volatility in the top decile of its past year - the one vol band two independent backtests found reliably positive (about +1pt over the next month, so it carries a 1.5 weight)."
        elif pct >= 70:
            lean = "good"
            note = f"{und_name} volatility elevated vs the past year - option premiums are rich, so the call-selling engine is well paid. Only the top decile scores, but the read is good."
        elif pct < 25:
            lean = "bad"
            note = f"{und_name} volatility near the bottom of its 1-year range - thin option premiums mean the income engine is earning less than usual. A lean, not a score: the old low-vol penalty didn't survive the audit."
        else:
            lean = "neutral"
            note = f"{und_name} volatility mid-range for the past year. Premiums ordinary."
        return {
            "name": f"{vol_name} vs its own past year",
            "value": f"{vol_level:.1f} (p{pct:.0f})",
            "score": s,
            "weight": W_VOL,
            "lean": lean,
            "note": note,
            "viz": viz(pct, ["calm year", "p90+ scores"], zones=[[90, 100, "good"]], marker=90),
        }

    signals.append(guarded(f"{vol_name} vs its own past year", sig_vol_percentile))

    # 2. VIX term structure (VIX / VIX3M) - broad-market vol curve;
    # relevant to both funds because panics are market-wide events.
    def sig_term_structure():
        vix_now = list(cached_rows("%5EVIX", "1y"))[-1][1]
        vix3m = list(cached_rows("%5EVIX3M", "5d"))[-1][1]
        ratio = vix_now / vix3m
        s = score_term_structure(ratio)
        if s:
            lean = "good"
            note = "VIX curve inverted - genuine panic. The backtest evidence on this band is mixed, but it fires rarely and coincides with real panics, so it keeps a modest 1.0 weight."
        elif ratio >= 0.93:
            lean = "good"
            note = "VIX curve flattening - near-term stress building toward the inversions that mark genuine panics. Reads good for a contrarian buyer; points only come at full inversion (1.00+)."
        else:
            lean = "neutral"
            note = "Normal upward-sloping VIX curve. No unusual near-term fear."
        return {
            "name": "VIX term structure (1mo/3mo)",
            "value": f"{ratio:.2f}",
            "score": s,
            "weight": W_TERM,
            "lean": lean,
            "note": note,
            "viz": viz((ratio - 0.70) / 0.40 * 100, ["0.70 calm", "1.10 panic"],
                       zones=[[75, 100, "good"]], marker=75),
        }

    signals.append(guarded("VIX term structure (1mo/3mo)", sig_term_structure))

    # 2b. GPIQ only: tech fear premium - VXN/VIX ratio ranked against its
    # own past year. Context only: the audit found the intuitive scoring
    # backwards (rich-tech-fear days went on to UNDERPERFORM), so it shows
    # but never scores.
    if fund["tech_premium"]:
        def sig_tech_premium():
            vix_rows = cached_rows("%5EVIX", "1y")
            vxn_rows = cached_rows("%5EVXN", "1y")
            vix_by_date = {d: c for d, c, _ in vix_rows}
            ratios = [c / vix_by_date[d] for d, c, _ in vxn_rows if d in vix_by_date]
            cur = ratios[-1]
            below = sum(1 for r in ratios if r < cur)
            pct = below / len(ratios) * 100
            if pct >= 65:
                lean = "bad"
                note = "Tech options pricier than usual relative to the broad market. Intuition says that's a contrarian buy signal - the audits found the opposite: rich tech fear premiums preceded weaker returns. So this reads bad, not good."
            elif pct < 20:
                lean = "good"
                note = "Tech options unusually cheap vs the broad market. Since the audits found rich tech premiums preceded weaker returns, an unusually cheap premium reads mildly good - though it never proved a scoring edge."
            else:
                lean = "neutral"
                note = "Tech vol priced normally relative to the broad market. The audits found rich tech premiums preceded weaker returns, so this only turns interesting at the extremes."
            return {
                "name": "Tech fear premium (VXN/VIX)",
                "value": f"{cur:.2f}x (p{pct:.0f})",
                "score": 0,
                "weight": 0,
                "lean": lean,
                "note": note,
                "viz": viz(pct, ["cheap tech vol", "rich tech vol"],
                           zones=[[0, 20, "good"], [65, 100, "bad"]]),
            }

        signals.append(guarded("Tech fear premium (VXN/VIX)", sig_tech_premium))

    # 3. Variance risk premium: implied vol minus underlying's realized vol.
    # Context only: both scored directions era-flipped in the audits.
    def sig_vrp():
        rets = [
            math.log(und_closes[i] / und_closes[i - 1])
            for i in range(len(und_closes) - 20, len(und_closes))
        ]
        realized = statistics.stdev(rets) * math.sqrt(252) * 100
        vrp = vol_level - realized
        if vrp >= 6:
            lean = "good"
            note = f"Options pricing far more turbulence than the {und_name} is delivering - the call-selling engine is well paid right now. A read only: the scored versions of this gap era-flipped in the audits."
        elif vrp <= -2:
            lean = "bad"
            note = f"Realized {und_name} swings exceed what options are pricing - call selling underpaid in this tape. A read only: the scored versions of this gap era-flipped in the audits."
        else:
            lean = "neutral"
            note = "Implied and realized volatility roughly in line - the call-selling engine earns its normal keep."
        return {
            "name": "Are call-sellers overpaid?",
            "value": f"{vrp:+.1f} pts",
            "score": 0,
            "weight": 0,
            "lean": lean,
            "note": note,
            "viz": viz((vrp + 4) / 12 * 100, ["underpaid −4", "overpaid +8"],
                       zones=[[0, 16.7, "bad"], [83.3, 100, "good"]]),
        }

    signals.append(guarded("Are call-sellers overpaid?", sig_vrp))

    # 4. Discount from 52-week high, on ADJUSTED closes. The single 3%+
    # band is the only discount that survived both audits (the >=7% "+2"
    # was a dot-com artifact; the at-the-high "-1" tested as noise).
    # Weight 1.5: +0.7-0.9pt/21d, era-stable on both indexes.
    s = score_drawdown(drawdown_adj)
    if s:
        dd_lean = "good"
        note = "A real 3%+ discount from the 52-week high - measured with distributions reinvested, so payouts don't masquerade as discounts. The one discount band that held up in both audits (+0.7-0.9pt over the next month, hence its 1.5 weight)."
    elif drawdown_adj <= 0.5:
        dd_lean = "neutral"
        note = "At (or within a whisker of) the 52-week high with distributions reinvested. Not a red flag: the audits found buying at the high performed perfectly fine on average."
    else:
        dd_lean = "neutral"
        note = "Barely below the 52-week high (distributions reinvested) - not the 3%+ discount the audits proved out, and not a warning either."
    signals.append({
        "name": "Discount from 52-week high", "value": f"{drawdown_adj:.1f}%",
        "score": s, "weight": W_DISCOUNT, "lean": dd_lean, "note": note,
        "viz": viz(drawdown_adj * 10, ["at the high", "−10%"],
                   zones=[[30, 100, "good"]], marker=30),
    })

    # 4b. Short-term index reversal (added in v3, top-weighted in v4): the
    # underlying proxy has
    # closed down 3+ straight sessions or fallen 3%+ over 5 sessions, on
    # adjusted closes. The one new rule that survived a 393-paper
    # literature review plus independent revalidation.
    und_sym = fund["underlying_symbol"]
    s = score_reversal(und_streak, und_ret5)
    if und_streak >= 3:
        rev_value = f"{und_streak} down days"
        rev_note = (
            f"{und_sym} has closed down {und_streak} straight sessions. Short selling streaks like this "
            f"have reliably bounced the next day - the market pays whoever provides liquidity into them. "
            f"Validated era-robust in our backtest ({fund['reversal_stats']} after the signal fires) - "
            f"the strongest edge this tool has, hence its top weight of 2.0."
        )
    elif s:
        rev_value = f"{und_ret5:+.1f}% in 5 sessions"
        rev_note = (
            f"{und_sym} is down {abs(und_ret5):.1f}% over the last 5 sessions. Sharp week-scale pullbacks "
            f"like this have reliably bounced - the market pays whoever provides liquidity into them. "
            f"Validated era-robust in our backtest ({fund['reversal_stats']} after the signal fires) - "
            f"the strongest edge this tool has, hence its top weight of 2.0."
        )
    else:
        rev_value = "no pullback"
        rev_note = (
            f"No short-term pullback in {und_sym} - this check pays only after 3 straight down closes or a "
            f"3%+ five-session drop, roughly 25-35 days a year. When it fires, next days averaged "
            f"{fund['reversal_stats']} in our validation, positive in every era - the strongest edge this "
            f"tool has, hence its top weight of 2.0."
        )
    signals.append({
        "name": "Short-term pullback (reversal)",
        "value": rev_value,
        "score": s,
        "weight": W_REVERSAL,
        "lean": "good" if s else "neutral",
        "note": rev_note,
    })

    # 5. Fund vs 50-day average (adjusted). Context only: the raw-close
    # version was largely a distribution artifact, and even measured
    # properly the band showed no reliable edge.
    if sma50_adj is None:
        note = "Not enough history for a 50-day average."
    elif vs_sma50_adj < 0:
        note = "Below its 50-day average (distributions reinvested). Shown for the curious - the audits found no predictive value either way in this line, so the read is a firm neutral."
    elif vs_sma50_adj > 3:
        note = "Stretched more than 3% above its 50-day average (distributions reinvested). Shown for the curious - the audits found no predictive value either way in this line, so the read is a firm neutral."
    else:
        note = "Close to its 50-day average (distributions reinvested). No predictive value either way per the audits - a firm neutral."
    signals.append({
        "name": f"{ticker} vs 50-day average", "value": f"{vs_sma50_adj:+.1f}%",
        "score": 0, "weight": 0, "lean": "neutral", "note": note,
        "viz": viz((vs_sma50_adj + 6) / 12 * 100, ["−6%", "+6%"]),
    })

    # 6. Underlying index regime. Never scores (the old below-200d "+1"
    # was negative on both SPY and QQQ), but the lean follows the evidence:
    # below the 200-day preceded below-average returns, so downtrend = bad.
    if und_above_200:
        note = f"{und_name} in a healthy uptrend (above its 200-day average) - the regime in which forward returns have been better on average."
    else:
        note = f"{und_name} below its 200-day average. The old rules gave this a point as \"bear pricing\" - the audits found the opposite: below-200-day days preceded below-average returns. So it reads bad (though nothing scores negative)."
    signals.append({
        "name": f"{und_name} regime",
        "value": "uptrend" if und_above_200 else "downtrend",
        "score": 0,
        "weight": 0,
        "lean": "good" if und_above_200 else "bad",
        "note": note,
    })

    # 7. High-yield credit spread (FRED BAMLH0A0HYM2). Only OAS >= 5%
    # scores; the widening "-1" is retired (its test days rebounded).
    def sig_credit():
        rows = cached_fred("BAMLH0A0HYM2")
        oas = rows[-1][1]
        prior = rows[max(0, len(rows) - 22)][1]
        chg_1mo = oas - prior
        s = score_credit(oas)
        if s:
            lean = "good"
            note = "Credit markets pricing serious stress - a contrarian buy zone. Principled but untested: spreads never got this wide in the testable sample, so its 1.0 weight rests on reasoning, not backtest evidence."
        elif chg_1mo >= 0.50:
            lean = "bad"
            note = "Credit spreads widening fast - stress building under the surface. The audit couldn't validate a score on this (those days actually rebounded), but as a plain read of conditions it leans bad."
        elif oas <= 3.5:
            lean = "neutral"
            note = "Credit markets calm - no stress."
        else:
            lean = "neutral"
            note = "Credit spreads moderately elevated but well short of the 5% panic band."
        return {
            "name": "Credit stress (junk-bond spreads)",
            "value": f"{oas:.2f}% ({chg_1mo:+.2f} 1mo)",
            "score": s,
            "weight": W_CREDIT,
            "lean": lean,
            "note": note,
            "viz": viz((oas - 2.0) / 4.0 * 100, ["2% calm", "6% crisis"],
                       zones=[[75, 100, "good"]], marker=75),
        }

    signals.append(guarded("Credit stress (junk-bond spreads)", sig_credit))

    # 8. Trailing payout vs riskless cash. Context only: a useful thing to
    # know about the fund, but never a proven timing signal.
    def sig_payout():
        if ttm_yield is None or tbill is None:
            raise ValueError("missing yield inputs")
        advantage = ttm_yield - tbill
        hi, mid = fund["payout_bands"]
        if advantage >= hi:
            lean = "good"
            note = f"{ticker}'s trailing payout beats riskless T-bills by a wide margin even for this fund - the income case is unusually strong. A read only: it never proved a timing edge."
        elif advantage >= mid:
            lean = "neutral"
            note = f"{ticker} yields comfortably more than cash - the normal state of affairs."
        else:
            lean = "bad"
            note = f"T-bills pay almost as much as {ticker} with zero risk - the income case is thinner than usual. A read only: the audit found no timing edge here."
        return {
            "name": "Payout vs safe cash",
            "value": f"{ttm_yield:.1f}% vs {tbill:.1f}% cash",
            "score": 0,
            "weight": 0,
            "lean": lean,
            "note": note,
            "viz": viz(advantage * 10, ["no edge over cash", "+10 pts"],
                       zones=[[hi * 10, 100, "good"], [0, mid * 10, "bad"]]),
        }

    signals.append(guarded("Payout vs safe cash", sig_payout))

    # 9. CNN Fear & Greed (contrarian). Only the <=25 extreme scores.
    def sig_fear_greed():
        score_, rating = cached_fear_greed()
        s = score_fear_greed(score_)
        if s:
            lean = "good"
            note = "Extreme fear across market internals - a contrarian extreme. Honesty note: CNN publishes only ~1 year of testable history, so its 1.0 weight rests mostly on principle."
        elif score_ <= 45:
            lean = "good"
            note = "Fear is the dominant mood - a mild contrarian positive, though short of the <=25 extreme that scores."
        elif score_ >= 75:
            lean = "bad"
            note = "Extreme greed - the crowd is all-in, historically a worse-than-average mood to add at. A read only: nothing scores negative anymore."
        else:
            lean = "neutral"
            note = "Sentiment in the normal range - no contrarian edge either way."
        return {
            "name": "Fear & Greed index",
            "value": f"{score_:.0f} ({rating})",
            "score": s,
            "weight": W_FG,
            "lean": lean,
            "note": note,
            "viz": viz(score_, ["extreme fear", "extreme greed"],
                       zones=[[0, 25, "good"], [75, 100, "bad"]], marker=25),
        }

    signals.append(guarded("Fear & Greed index", sig_fear_greed))

    # 10. Event calendar: FOMC decisions + CPI prints (informational, score 0)
    next_fomc = next((d for d in FOMC_DATES if date.fromisoformat(d) >= today), None)
    next_cpi = next((d for d in CPI_DATES if date.fromisoformat(d) >= today), None)
    days_to_fomc = (date.fromisoformat(next_fomc) - today).days if next_fomc else None
    days_to_cpi = (date.fromisoformat(next_cpi) - today).days if next_cpi else None
    events = [(d, "FOMC", next_fomc) for d in [days_to_fomc] if d is not None]
    events += [(d, "CPI", next_cpi) for d in [days_to_cpi] if d is not None]
    events.sort()
    imminent = bool(events) and events[0][0] <= 2
    if imminent and events[0][1] == "CPI":
        ev_note = "CPI print within 2 days - the biggest single-day volatility event most months. Not a reason to skip a scheduled buy, but expect a swing."
    elif imminent:
        ev_note = "Rate decision within 2 days - a volatility event ahead. Not a reason to skip a scheduled buy, but expect a swing either way."
    else:
        parts = []
        if next_cpi:
            parts.append(f"next CPI {next_cpi}")
        if next_fomc:
            parts.append(f"next FOMC {next_fomc}")
        ev_note = "No event risk in the next few days" + (" (" + ", ".join(parts) + ")." if parts else ".")
    ev_value = f"{events[0][1]} in {events[0][0]}d" if events else "n/a"
    signals.append({
        "name": "Event calendar",
        "value": ev_value,
        "score": 0,
        "weight": 0,
        "lean": "bad" if imminent else "neutral",
        "note": ev_note,
    })

    # ---- verdict (v4: three bands on the weighted composite W, nothing
    # negative - the audits showed the tool couldn't spot bad days, so it
    # stopped claiming to). Band mapping: W >= 3 good, W >= 1 ok, W = 0
    # neutral; headline buy score = round(50 + W * 6.25). ----
    score = round(sum(x["score"] for x in signals), 2)
    score100 = score100_of(score)
    tone = verdict_tone(score)
    verdict = {
        "good": {
            "label": "Better-than-usual entry",
            "summary": f"Heavily-weighted rare conditions aligned today - the kind of setup that has historically rewarded buyers. If you have cash earmarked for {ticker}, this is a sensible day to deploy it.",
        },
        "ok": {
            "label": "Mild tailwind - fine day to buy",
            "summary": "A validated rare condition is in your favor today. Nothing dramatic, but no reason to wait either.",
        },
        "neutral": {
            "label": "No edge either way - buy on schedule",
            "summary": "None of the rare conditions this tool watches for are present. If it's your scheduled buy day, buy - waiting for a better day costs more than it saves, on average.",
        },
    }[tone]
    verdict["tone"] = tone
    verdict["score"] = score
    verdict["score100"] = score100
    verdict["weights_max"] = WEIGHTS_MAX

    # ---- "what would flip the verdict": nearest actionable thresholds.
    # v4: only the six weighted bands can move the verdict, and all of them
    # move it UP - there are no negative flips anymore. Each row states its
    # impact on the headline 0-100 buy score (weight * 6.25 points). ----
    def build_flips() -> list[dict]:
        cands = []

        def add(dist, label, text):
            cands.append({"dist": dist, "direction": +1, "label": label, "text": text})

        def pts(w: float) -> str:
            return f"+{round(w * 6.25)} pts"

        # Adjusted discount reaching 3%. The threshold lives in
        # total-return space; translate it to a raw price so the number is
        # recognizable from a brokerage screen.
        if drawdown_adj < 3:
            move = high52_adj * 0.97 / adj_price - 1
            t = price * (1 + move)
            add(abs(move) * 100, "Real discount",
                f"{ticker} at ${t:.2f} ({move * 100:+.1f}% from here) = 3%+ below the 52-week high with distributions reinvested ({pts(W_DISCOUNT)})")

        # Short-term reversal - only shown when within striking distance
        # (2-day streak, or a 5-session return within 1.5% of the -3% band).
        if score_reversal(und_streak, und_ret5) == 0:
            if und_streak == 2:
                add(0.5, "Short-term pullback",
                    f"{und_sym} has fallen 2 straight sessions - one more down close = short-term reversal ({pts(W_REVERSAL)})")
            elif und_ret5 is not None and und_ret5 <= -1.5:
                need = abs(-3.0 - und_ret5)
                add(need, "Short-term pullback",
                    f"{und_sym} 5-session return {und_ret5:+.1f}% - another -{need:.1f}% = short-term reversal ({pts(W_REVERSAL)})")

        # Vol index reaching the top decile of its trailing year
        svol = sorted(vol_closes)
        nv = len(svol)
        vpct = sum(1 for c in vol_closes if c < vol_level) / nv * 100
        if vpct < 90:
            lvl = svol[min(nv - 1, int(0.90 * nv))]
            add(abs(lvl / vol_level - 1) * 100, "Top-decile premiums",
                f"{vol_name} above {lvl:.1f} (now {vol_level:.1f}) = top decile of its past year ({pts(W_VOL)})")

        # VIX term structure inverting
        try:
            vix_now = list(cached_rows("%5EVIX", "1y"))[-1][1]
            vix3m = list(cached_rows("%5EVIX3M", "5d"))[-1][1]
            r = vix_now / vix3m
            if r < 1.00:
                add((1.00 / r - 1) * 100, "VIX curve inversion",
                    f"VIX/VIX3M ratio at 1.00 (now {r:.2f}) = inverted vol curve, genuine panic ({pts(W_TERM)})")
        except Exception:
            pass

        # Credit OAS reaching the 5% panic band
        try:
            oas = cached_fred("BAMLH0A0HYM2")[-1][1]
            if oas < 5.0:
                add((5.0 / oas - 1) * 100, "Extreme credit stress",
                    f"Junk-bond spreads at 5.00% (now {oas:.2f}%) = extreme credit stress ({pts(W_CREDIT)})")
        except Exception:
            pass

        # Fear & Greed reaching the contrarian extreme
        try:
            fg_now, _ = cached_fear_greed()
            if fg_now > 25:
                add(fg_now - 25, "Extreme fear",
                    f"Fear & Greed at 25 or below (now {fg_now:.0f}) = extreme fear, a contrarian buy zone ({pts(W_FG)})")
        except Exception:
            pass

        cands.sort(key=lambda c: c["dist"])
        return [{"label": c["label"], "direction": c["direction"], "text": c["text"]} for c in cands[:5]]

    try:
        flips = build_flips()
    except Exception:
        flips = []

    # ---- score history: backfill missing dates + append today's live row ----
    live_drivers = [
        f"{fmt_w(s['score'])} {s['name']}"
        for s in sorted((x for x in signals if x["score"]), key=lambda x: -abs(x["score"]))[:3]
    ]
    live_row = {
        "date": fund_rows[-1][0].isoformat(),
        "score": score,
        "score100": score100,
        "tone": tone,
        "price": price,
        "adj": fund_rows[-1][2],
        "backfilled": False,
        "drivers": live_drivers,
    }
    hist_path = DOCS / fund["history_out"]
    try:
        history = build_history(fund, fund_rows, live_row)
    except Exception:
        # Backfill must never break the daily build: fall back to just
        # appending today's row to whatever history already exists.
        history = load_history(hist_path)
        if upsert_live_row(history, live_row):
            save_history(hist_path, history)
    tone_by_date = {r["date"]: r["tone"] for r in history}
    s100_by_date = {r["date"]: r.get("score100") for r in history}

    meter_min, meter_max = fund["meter"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_version": RULES_VERSION,
        "ticker": ticker,
        "verdict": verdict,
        "meter": {"min": meter_min, "max": meter_max},
        "signals": signals,
        "fund": {
            "price": price,
            "day_change_pct": round(day_change, 2),
            "sma50": round(sma50, 2) if sma50 else None,
            "sma200": round(sma200, 2) if sma200 else None,
            "high52w": high52,
            "drawdown_pct": drawdown,
            "series": [
                {"d": d.isoformat(), "c": c, "t": tone_by_date.get(d.isoformat()),
                 "s": s100_by_date.get(d.isoformat())}
                for d, c, _ in fund_rows[-180:]
            ],
        },
        "flips": flips,
        "report_card": report_card(history) if history else None,
        "income": {"ttm_yield_pct": ttm_yield, "tbill_3mo": tbill},
        "distributions": distributions_block(div_events, today),
        "underlying": {
            "symbol": fund["underlying_symbol"],
            "price": und_price,
            "sma200": round(und_sma200, 2) if und_sma200 else None,
            "above_200d": und_above_200,
        },
        "vol": {"index": vol_name, "level": vol_level},
        "fomc": {"next": next_fomc, "days_until": days_to_fomc, "imminent": imminent},
        "cpi": {"next": next_cpi, "days_until": days_to_cpi},
        "backtest": backtest([(d, a) for d, _, a in fund_rows]),
        "headlines": fetch_headlines(fund["news_query"]),
    }


def build_stock_history(fund: dict, rows: list, rv: list, live_row: dict) -> list[dict]:
    """Backfill for single stocks: same as-of discipline as the fund
    backfill, but scored with the stock rules (which are all derived from
    the stock's own series - no external fetches to guard).

    Rows before WARMUP_SESSIONS of lead-in are skipped rather than scored on a
    truncated window: a 52-week high taken over 30 sessions is not a 52-week
    high, and would hand out at-the-high or falling-knife points that the data
    can't support. Normally the history file already covers those dates, but a
    RULES_VERSION bump throws the file away and rebuilds it from whatever the
    fetch range happens to reach, which is exactly when the guard matters.
    """
    path = DOCS / fund["history_out"]
    hist = load_history(path)
    have = {r["date"] for r in hist}
    start = date.fromisoformat(fund["history_start"]) if fund["history_start"] else None
    adj = [a for _, _, a in rows]
    bands = fund["bands"]

    changed = False
    for i in range(len(rows) - 1):
        d = rows[i][0]
        if (start and d < start) or (bands and i < WARMUP_SESSIONS):
            continue
        iso = d.isoformat()
        if iso in have:
            continue
        sc: list[tuple[str, float]] = []
        if bands:
            streak, ret5 = reversal_inputs(adj, i)
            hi52 = max(adj[max(0, i - 251): i + 1])
            dd = round((1 - adj[i] / hi52) * 100, 1)
            sc = [
                ("Short-term pullback (reversal)", score_stock_pullback(streak, ret5, bands)),
                ("Realized volatility vs its past year",
                 score_stock_rvol(trailing_pct(rv, i), bands)),
                ("Distance from 52-week high", score_stock_high_or_knife(dd, bands)),
            ]
        total = round(sum(s for _, s in sc), 2)
        drivers = [
            f"{fmt_w(s)} {n}"
            for n, s in sorted((x for x in sc if x[1]), key=lambda x: -abs(x[1]))[:3]
        ]
        hist.append({
            "date": iso,
            "score": total,
            "score100": score100_of(total),
            "tone": verdict_tone_stock(total) if bands else "neutral",
            "price": rows[i][1],
            "adj": rows[i][2],
            "backfilled": True,
            "drivers": drivers,
        })
        have.add(iso)
        changed = True
    changed = upsert_live_row(hist, live_row) or changed
    if changed or not path.exists():
        save_history(path, hist)
    else:
        hist.sort(key=lambda r: r["date"])
    return hist


def build_stock(fund: dict) -> dict:
    """Single-stock page build.

    Which bands score is per ticker: TSLA scores all five its own pass proved,
    NVDA the two that survived its pass, GOOG the one that survived its, SPCX
    none (it has ~2 months of history). Bands a ticker's pass rejected still
    render - with the numbers that rejected them - but carry score 0.

    Deliberately leaner than the fund build, and every 1-year-window signal
    carries a min-history guard so SPCX shows honest "too young" cards instead
    of fake percentiles.
    """
    ticker = fund["ticker"]
    name = fund["asset_name"]
    bands = fund["bands"]
    validated = bool(bands)
    failed = fund["band_failed"]
    stats = fund["band_stats"]

    result = yahoo_chart(ticker, fund["range"], events=True)
    rows = chart_rows(result)
    closes = [c for _, c, _ in rows]
    adj = [a for _, _, a in rows]
    n = len(rows)
    too_young = n < 252

    price = closes[-1]
    day_change = (price / closes[-2] - 1) * 100 if n > 1 else 0.0
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    year = closes[-252:]
    high52 = max(year)
    drawdown = round((1 - price / high52) * 100, 1)
    year_adj = adj[-252:]
    high52_adj = max(year_adj)
    drawdown_adj = round((1 - adj[-1] / high52_adj) * 100, 1)

    streak, ret5 = reversal_inputs(adj, n - 1)
    rv = realized_vol_series(adj)
    rv_now = rv[-1]
    rv_pct = trailing_pct(rv, n - 1)

    today = date.today()
    high_label = "52-week high" if not too_young else "high since IPO"

    signals = []

    # 1. Short-term pullback. Thresholds come from the TSLA pass; whether they
    # score here is decided by this ticker's own pass (fund["bands"]).
    s = score_stock_pullback(streak, ret5, bands)
    if streak >= 3:
        pb_value = f"{streak} down days"
    elif ret5 is not None and ret5 <= -3:
        pb_value = f"{ret5:+.1f}% in 5 sessions"
    else:
        pb_value = "no pullback"
    if not validated:
        pb_note = (
            f"{ticker} has traded for {n} sessions - far too few to validate anything. On TSLA "
            f"(16 years of history) sharp pullbacks reliably bounced, but single names differ and "
            f"nothing may be assumed to transfer. Context only until enough history exists to test."
        )
        pb_lean = "neutral"
    elif s == WS_CRASH:
        pb_note = (
            f"{ticker} is down {abs(ret5):.1f}% over 5 sessions - the crash band (5d ≤ −12%). "
            f"On {ticker}'s own history these days ran {stats['crash']}, so the band carries the "
            f"heaviest single-stock weight, 2.0."
        )
        pb_lean = "good"
    elif s == WS_DOWN3:
        pb_note = (
            f"{ticker} has closed down {streak} straight sessions. On {ticker}'s own history that "
            f"ran {stats['down3']} - a real but modest edge, so it earns 1.0."
        )
        pb_lean = "good"
    else:
        pays = []
        if "down3" in bands:
            pays.append("3 straight down closes (+1.0)")
        if "crash" in bands:
            pays.append("a 12%+ five-session drop (+2.0)")
        pb_note = (
            f"No qualifying pullback. This check pays after {' or '.join(pays)}. The index pages' "
            f"milder −3% five-session band is not used on a single name: it would fire on a large "
            f"share of days and tested as noise."
        )
        if failed.get("crash"):
            pb_note += " " + failed["crash"]
        pb_lean = "neutral"
    pb_sig = {
        "name": "Short-term pullback (reversal)",
        "value": pb_value,
        "score": s,
        "weight": WS_CRASH if "crash" in bands else (WS_DOWN3 if "down3" in bands else 0),
        "lean": pb_lean,
        "note": pb_note,
    }
    if ret5 is not None:
        scores_crash = "crash" in bands
        pb_sig["viz"] = viz(
            (ret5 + 15) / 15 * 100, ["−15% in 5d", "flat"],
            zones=[[0, 20, "good"]] if scores_crash else None,
            marker=20 if scores_crash else None,
        )
    signals.append(pb_sig)

    # 2. Realized volatility vs its own past year (the single-stock
    # substitute for a vol index - no keyless TSLA IV series exists).
    s = score_stock_rvol(rv_pct, bands)
    scores_rvol = "rvol_p90" in bands
    if rv_now is None:
        rvol_value = "n/a"
        rvol_lean = "neutral"
        rvol_note = f"Fewer than 21 sessions of history - realized volatility isn't computable yet."
    elif rv_pct is None:
        rvol_value = f"{rv_now:.0f}% ann."
        rvol_lean = "neutral"
        rvol_note = (
            f"20-day realized volatility, annualized. A 1-year percentile needs a year of history - "
            f"{ticker} IPO'd {fund.get('ipo', 'recently')}, so this card can't rank the number until "
            f"mid-2027. The raw level is shown for context."
        )
    elif s:
        rvol_value = f"{rv_now:.0f}% ann. (p{rv_pct:.0f})"
        rvol_lean = "good"
        rvol_note = (
            f"{ticker}'s realized volatility is in the top decile of its own past year. On {ticker}'s "
            f"own history these days ran {stats['rvol_p90']} - chaos has been the single-name buyer's "
            f"friend here. Weight 1.5."
        )
    elif not scores_rvol and validated:
        rvol_value = f"{rv_now:.0f}% ann. (p{rv_pct:.0f})"
        rvol_lean = "neutral"
        rvol_note = (
            f"20-day realized volatility ranked against {ticker}'s own past year. This band scores "
            f"+1.5 on TSLA, but it was tested on {ticker} and rejected: {failed['rvol_p90']} "
            f"Shown as context; it cannot move the score."
        )
    elif rv_pct >= 70:
        rvol_value = f"{rv_now:.0f}% ann. (p{rv_pct:.0f})"
        rvol_lean = "good"
        rvol_note = (
            f"Realized volatility elevated vs {ticker}'s own past year. Only the top decile scores, "
            f"but the direction of the evidence says turbulent tapes have been better entries."
        )
    elif rv_pct < 25:
        rvol_value = f"{rv_now:.0f}% ann. (p{rv_pct:.0f})"
        rvol_lean = "bad"
        rvol_note = (
            f"An unusually quiet tape for {ticker}. Quiet periods leaned slightly below average in "
            f"validation but didn't meet the scoring bar - a lean, not a score."
        )
    else:
        rvol_value = f"{rv_now:.0f}% ann. (p{rv_pct:.0f})"
        rvol_lean = "neutral"
        rvol_note = f"Realized volatility mid-range for {ticker}'s own past year."
    rvol_sig = {
        "name": "Realized volatility vs its past year",
        "value": rvol_value,
        "score": s,
        "weight": WS_RVOL if scores_rvol else 0,
        "lean": rvol_lean,
        "note": rvol_note,
    }
    if rv_pct is not None:
        rvol_sig["viz"] = viz(rv_pct, ["quiet year", "p90+ scores" if scores_rvol else "busy year"],
                              zones=[[90, 100, "good"]] if scores_rvol else None,
                              marker=90 if scores_rvol else None)
    signals.append(rvol_sig)

    # 3. Distance from the 52-week high. On TSLA the evidence INVERTS the index
    # intuition - at the high is momentum (+1.0) and the 10-20% band is the
    # falling knife (-1.5, the project's one evidence-backed bad zone. Neither
    # band survived its own test on NVDA or GOOG, where both render as context.
    s = score_stock_high_or_knife(drawdown_adj, bands)
    scores_high = "at_high" in bands
    scores_knife = "knife" in bands
    if not validated:
        dd_lean = "neutral"
        dd_note = (
            f"{ticker} is {drawdown_adj:.1f}% below its high since the June 2026 IPO. A real 52-week "
            f"high won't exist until June 2027, and no drawdown band has been validated on this stock. "
            f"Context only."
        )
    elif s == WS_HIGH:
        dd_lean = "good"
        dd_note = (
            f"{ticker} is at (or within a whisker of) its 52-week high. For this stock that is "
            f"MOMENTUM, not a warning: at-the-high days ran {stats['at_high']} - the opposite of what "
            f"this line means for the index funds. Weight 1.0."
        )
    elif s == WS_KNIFE:
        dd_lean = "bad"
        dd_note = (
            f"{ticker} sits {drawdown_adj:.1f}% below its 52-week high - the falling-knife band "
            f"(10-20% down), {stats['knife']}. The one zone in this entire project with era-robust "
            f"evidence of BELOW-average forward returns. Single names in mid-drawdown lean momentum, "
            f"not mean-reversion. Weight -1.5."
        )
    elif not (scores_high or scores_knife):
        # Both drawdown bands were tested on this ticker and rejected: say which
        # one today's reading would have tripped, and why it no longer counts.
        which = "knife" if 10.0 <= drawdown_adj < 20.0 else ("at_high" if drawdown_adj <= 0.5 else None)
        dd_lean = "neutral"
        dd_note = (
            f"{ticker} is {drawdown_adj:.1f}% below its 52-week high. Neither single-stock drawdown "
            f"band scores here: both were tested on {ticker}'s own history and rejected. "
        )
        if which:
            dd_note += failed[which] + " "
        dd_note += (
            "The index funds' 3%+ discount band does not transfer to single names either, so this "
            "card informs and never scores."
        )
    elif drawdown_adj >= 35:
        dd_lean = "good"
        dd_note = (
            f"A 35%+ drawdown - the deep-value zone. Validation showed +13pt over the next quarter on "
            f"average, but the effect was era-fragile (t=1.1) and narrowly missed the scoring bar. "
            f"Reads good; scores nothing."
        )
    elif drawdown_adj >= 20:
        dd_lean = "neutral"
        dd_note = (
            f"A 20-35% drawdown - past the falling-knife band, short of deep value. 16 years of "
            f"history show no reliable edge either way in this no-man's-land."
        )
    else:
        dd_lean = "neutral"
        dd_note = (
            f"A modest {drawdown_adj:.1f}% off the 52-week high. Note the index funds' 3%+ discount "
            f"band does NOT transfer: on the calibrated single-stock pass it tested as noise, so no "
            f"points for small dips here."
        )
    dd_zones = []
    if scores_high:
        dd_zones.append([0, 1, "good"])
    if scores_knife:
        dd_zones.append([20, 40, "bad"])
    if validated:
        dd_zones.append([70, 100, "good"])
    signals.append({
        "name": f"Distance from {high_label}",
        "value": f"{drawdown_adj:.1f}%",
        "score": s,
        "weight": WS_KNIFE if s == WS_KNIFE else (WS_HIGH if scores_high else 0),
        "lean": dd_lean,
        "note": dd_note,
        "viz": viz(
            drawdown_adj * 2, ["at the high", "−50%"],
            zones=dd_zones or None,
        ),
    })

    # 4. Own 200-day regime (TSLA) / trading-history card (SPCX).
    if sma200 is not None:
        above = price >= sma200
        signals.append({
            "name": f"{ticker} regime",
            "value": "uptrend" if above else "downtrend",
            "score": 0,
            "weight": 0,
            "lean": "good" if above else "bad",
            "note": (
                f"{ticker} {'above' if above else 'below'} its own 200-day average. Descriptive only - "
                f"the fund audits showed below-200-day is not a buy signal, and no regime band was "
                f"validated on the single-stock pass either. The lean just reads the tape."
            ),
        })
    else:
        unlock = []
        if sma50 is None:
            unlock.append("50-day average needs ~2.5 months")
        unlock.append("200-day average arrives ~March 2027")
        unlock.append("1-year signals (52-week high, vol percentile) arrive June 2027")
        signals.append({
            "name": "Trading history",
            "value": f"{n} sessions",
            "score": 0,
            "weight": 0,
            "lean": "neutral",
            "note": (
                f"{name} has traded {n} sessions since its {fund.get('ipo', '2026')} IPO. "
                f"What unlocks as history accumulates: " + "; ".join(unlock) + ". Until then this "
                f"page is context, not signal."
            ),
        })

    # 5. Market backdrop - shown because people ask, scored never: the
    # validation pass tested market-wide panic conditions (VIX >= p90,
    # curve inversion, extreme fear) directly against TSLA's forward
    # returns and every one era-flipped.
    def sig_market():
        vix_rows = list(cached_rows("%5EVIX", "1y"))
        vix_level = vix_rows[-1][1]
        vix_closes = [c for _, c, _ in vix_rows]
        pct = sum(1 for c in vix_closes if c < vix_level) / len(vix_closes) * 100
        fg_score, fg_rating = cached_fear_greed()
        return {
            "name": "Market backdrop (VIX & fear)",
            "value": f"VIX {vix_level:.1f} (p{pct:.0f}), F&G {fg_score:.0f}",
            "score": 0,
            "weight": 0,
            "lean": "neutral",
            "note": (
                f"The market-wide panic conditions that score for the index funds (VIX top-decile, "
                f"curve inversion, Fear & Greed <= 25) were tested directly against single-stock "
                f"forward returns on the calibrated name - and every one era-flipped. Single-company "
                f"risk swamps macro timing, so this card informs and never scores."
            ),
        }

    signals.append(guarded("Market backdrop (VIX & fear)", sig_market))

    # 6. Event calendar: earnings first (the single-stock volatility event),
    # then FOMC/CPI. Earnings fetch is guarded - omit with a note if the
    # crumb handshake ever breaks.
    next_fomc = next((d for d in FOMC_DATES if date.fromisoformat(d) >= today), None)
    next_cpi = next((d for d in CPI_DATES if date.fromisoformat(d) >= today), None)
    days_to_fomc = (date.fromisoformat(next_fomc) - today).days if next_fomc else None
    days_to_cpi = (date.fromisoformat(next_cpi) - today).days if next_cpi else None
    earnings_iso = None
    earnings_est = False
    earnings_note = ""
    try:
        got = fetch_earnings(ticker)
        if got:
            earnings_iso, earnings_est = got
    except Exception:
        earnings_note = " (Earnings date unavailable today - Yahoo's calendar endpoint didn't answer.)"
    days_to_earnings = (date.fromisoformat(earnings_iso) - today).days if earnings_iso else None

    events = [(d, "earnings", earnings_iso) for d in [days_to_earnings] if d is not None]
    events += [(d, "FOMC", next_fomc) for d in [days_to_fomc] if d is not None]
    events += [(d, "CPI", next_cpi) for d in [days_to_cpi] if d is not None]
    events.sort()
    imminent = bool(events) and events[0][0] <= 2
    earnings_imminent = days_to_earnings is not None and days_to_earnings <= 5
    if earnings_imminent:
        est = " (date still an estimate)" if earnings_est else ""
        ev_note = (
            f"Earnings on {earnings_iso}{est} - THE single-stock volatility event: {ticker}-sized "
            f"names routinely gap 5-15% on results, dwarfing any timing signal on this page. Not a "
            f"reason to abandon a plan, but know it's a coin-flip week."
        )
        ev_lean = "bad"
    elif imminent:
        what = "CPI print" if events[0][1] == "CPI" else "Rate decision"
        ev_note = f"{what} within 2 days - a market-wide volatility event. Expect a swing either way."
        ev_lean = "bad"
    else:
        parts = []
        if earnings_iso:
            parts.append(f"earnings {earnings_iso}" + (" est." if earnings_est else ""))
        if next_cpi:
            parts.append(f"CPI {next_cpi}")
        if next_fomc:
            parts.append(f"FOMC {next_fomc}")
        ev_note = "No event risk in the next few days" + (" (" + ", ".join(parts) + ")." if parts else ".") + earnings_note
        ev_lean = "neutral"
    ev_value = f"{events[0][1]} in {events[0][0]}d" if events else "n/a"
    signals.append({
        "name": "Event calendar",
        "value": ev_value,
        "score": 0,
        "weight": 0,
        "lean": ev_lean,
        "note": ev_note,
    })

    # ---- verdict ----
    score = round(sum(x["score"] for x in signals), 2)
    score100 = score100_of(score)
    tone = verdict_tone_stock(score) if validated else "neutral"
    if not validated:
        verdict = {
            "label": "Too young to score - context only",
            "summary": (
                f"{name} has ~{n} sessions of trading history. Nothing can be validated on that, so "
                f"nothing scores and the buy score pins at 50. This page is context, not signal, until "
                f"enough history accumulates to test (percentile signals arrive mid-2027). For a "
                f"single company, position sizing matters far more than timing anyway."
            ),
        }
    else:
        # How much of TSLA's five-band framework survived this ticker's own
        # test. Stated on every verdict so a thin scorer never looks like a
        # rich one.
        rejected = len(failed)
        scope = (
            f" {ticker} scores {len(bands)} of the 5 single-stock bands: the other {rejected} "
            f"were tested on {ticker}'s own history and rejected, so they show as context only."
            if rejected else ""
        )
        verdict = {
            "good": {
                "label": "Better-than-usual entry",
                "summary": (
                    f"Rare validated conditions aligned on {ticker} today - the kind of setup that "
                    f"rewarded buyers across {ticker}'s own history. If you were planning "
                    f"to add, this is a sensible day. (Still one company: size the position accordingly.)"
                    f"{scope}"
                ),
            },
            "ok": {
                "label": "Mild tailwind - fine day to buy",
                "summary": (
                    f"A condition validated on {ticker}'s own history is in your favor today. Nothing "
                    f"dramatic - and single-stock risk still dwarfs entry timing."
                    f"{scope}"
                ),
            },
            "neutral": {
                "label": "No edge either way",
                "summary": (
                    f"None of the scored {ticker} conditions are present. Unlike the index funds, this "
                    f"page can't add \"any day is fine\": that logic was proven on diversified indexes, "
                    f"not single companies."
                    f"{scope}"
                ),
            },
            "caution": {
                "label": "Worse-than-average zone",
                "summary": (
                    f"{ticker} sits in its 10-20% drawdown band - the falling knife. This is the one "
                    f"zone in this whole project with era-robust evidence of below-average forward "
                    f"returns: single names in mid-drawdown tend to keep drifting, not bounce. Nothing "
                    f"here says sell; it says this specific dip has historically kept dipping."
                    f"{scope}"
                ),
            },
        }[tone]
    verdict["tone"] = tone
    verdict["score"] = score
    verdict["score100"] = score100
    verdict["weights_max"] = stock_weight_range(bands)[1]

    # ---- flips (validated stocks only; SPCX has nothing to flip) ----
    def build_flips() -> list[dict]:
        if not validated:
            return []
        cands = []

        def add(dist, direction, label, text):
            cands.append({"dist": dist, "direction": direction, "label": label, "text": text})

        def pts(w: float) -> str:
            return f"{'+' if w > 0 else '−'}{abs(round(w * 6.25))} pts"

        cur_pb = score_stock_pullback(streak, ret5, bands)
        if ("crash" in bands and cur_pb < WS_CRASH and ret5 is not None
                and -12 < ret5 <= -6):
            need = abs(-12.0 - ret5)
            add(need, +1, "Crash band",
                f"{ticker} 5-session return {ret5:+.1f}% - another -{need:.1f}% reaches the validated crash band ({pts(WS_CRASH - cur_pb)})")
        if "down3" in bands and cur_pb == 0 and streak == 2:
            add(0.5, +1, "Short-term pullback",
                f"{ticker} has fallen 2 straight sessions - one more down close = pullback signal ({pts(WS_DOWN3)})")

        if "rvol_p90" in bands and rv_pct is not None and rv_pct < 90 and rv_now is not None:
            window = sorted(x for x in rv[-252:] if x is not None)
            lvl = window[min(len(window) - 1, int(0.90 * len(window)))]
            add(abs(lvl / rv_now - 1) * 100, +1, "Top-decile volatility",
                f"20-day realized vol above {lvl:.0f}% annualized (now {rv_now:.0f}%) = top decile of its year ({pts(WS_RVOL)})")

        if "at_high" in bands and 0.5 < drawdown_adj <= 3.5:
            move = high52_adj * 0.995 / adj[-1] - 1
            add(abs(move) * 100, +1, "Momentum: new high",
                f"{ticker} at ${price * (1 + move):.2f} ({move * 100:+.1f}% from here) = back within 0.5% of the 52-week high ({pts(WS_HIGH)})")

        if "knife" in bands and 6.5 <= drawdown_adj < 10:
            move = high52_adj * 0.90 / adj[-1] - 1
            add(abs(move) * 100, -1, "Falling-knife zone",
                f"{ticker} at ${price * (1 + move):.2f} ({move * 100:+.1f}% from here) enters the 10-20% falling-knife band ({pts(WS_KNIFE)})")
        if "knife" in bands and 10 <= drawdown_adj < 20:
            move = high52_adj * 0.90 / adj[-1] - 1
            add(abs(move) * 100, +1, "Exiting the knife zone",
                f"{ticker} at ${price * (1 + move):.2f} ({move * 100:+.1f}% from here) climbs out of the falling-knife band ({pts(-WS_KNIFE)})")

        cands.sort(key=lambda c: c["dist"])
        return [{"label": c["label"], "direction": c["direction"], "text": c["text"]} for c in cands[:5]]

    try:
        flips = build_flips()
    except Exception:
        flips = []

    # ---- history + report card ----
    live_drivers = [
        f"{fmt_w(x['score'])} {x['name']}"
        for x in sorted((x for x in signals if x["score"]), key=lambda x: -abs(x["score"]))[:3]
    ]
    live_row = {
        "date": rows[-1][0].isoformat(),
        "score": score,
        "score100": score100,
        "tone": tone,
        "price": price,
        "adj": rows[-1][2],
        "backfilled": False,
        "drivers": live_drivers,
    }
    hist_path = DOCS / fund["history_out"]
    try:
        history = build_stock_history(fund, rows, rv, live_row)
    except Exception:
        history = load_history(hist_path)
        if upsert_live_row(history, live_row):
            save_history(hist_path, history)
    tone_by_date = {r["date"]: r["tone"] for r in history}
    s100_by_date = {r["date"]: r.get("score100") for r in history}

    tbill = None
    try:
        tbill = round(cached_fred("DGS3MO")[-1][1], 2)
    except Exception:
        pass

    meter_min, meter_max = stock_meter(bands)
    rc_tones = stock_tones(bands)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_version": RULES_VERSION,
        "ticker": ticker,
        "asset": {
            "kind": "stock",
            "name": name,
            "ipo": fund.get("ipo"),
            "sessions": n,
            "too_young": too_young,
            "validated": validated,
            # Which of the five single-stock bands survived THIS ticker's own
            # validation pass. The pages render their scoring copy from this,
            # so a page can never claim an edge the ticker didn't earn.
            "bands_scored": list(bands),
            "bands_rejected": sorted(failed),
            "has_negative_band": "knife" in bands,
        },
        "verdict": verdict,
        "meter": {"min": meter_min, "max": meter_max},
        "signals": signals,
        "fund": {
            "price": price,
            "day_change_pct": round(day_change, 2),
            "sma50": round(sma50, 2) if sma50 else None,
            "sma200": round(sma200, 2) if sma200 else None,
            "high52w": high52,
            "high_label": high_label,
            "drawdown_pct": drawdown,
            "series": [
                {"d": d.isoformat(), "c": c, "t": tone_by_date.get(d.isoformat()),
                 "s": s100_by_date.get(d.isoformat())}
                for d, c, _ in rows[-180:]
            ],
        },
        "flips": flips,
        "report_card": report_card(history, rc_tones) if history else None,
        "income": {"ttm_yield_pct": None, "tbill_3mo": tbill},
        "underlying": {
            "symbol": ticker,
            "price": price,
            "sma200": round(sma200, 2) if sma200 else None,
            "above_200d": sma200 is not None and price >= sma200,
        },
        "vol": {"index": "20d realized", "level": round(rv_now, 1) if rv_now else None},
        "fomc": {"next": next_fomc, "days_until": days_to_fomc, "imminent": imminent},
        "cpi": {"next": next_cpi, "days_until": days_to_cpi},
        "earnings": {"next": earnings_iso, "days_until": days_to_earnings, "estimate": earnings_est},
        "backtest": backtest([(d, a) for d, _, a in rows]),
        "headlines": fetch_headlines(fund["news_query"]),
    }


if __name__ == "__main__":
    DOCS.mkdir(parents=True, exist_ok=True)
    for fund in FUNDS:
        data = build_stock(fund) if fund.get("kind") == "stock" else build(fund)
        out = DOCS / fund["out"]
        out.write_text(json.dumps(data, indent=1))
        print(f"wrote {out}")
        print(
            f"{fund['ticker']} verdict: {data['verdict']['label']} "
            f"(buy score {data['verdict']['score100']}/100, W={data['verdict']['score']:g}/{data['verdict']['weights_max']:g})"
        )
        for s in data["signals"]:
            print(f"  {fmt_w(s['score']):>4} [{s.get('lean', '?'):>7}] {s['name']}: {s['value']}")
        bt = data["backtest"]
        print(f"  backtest: DCA {bt['dca_return_pct']}% vs dip-waiting {bt['dip_return_pct']}%")
        rc = data.get("report_card")
        if rc:
            print(f"  history: {rc['days_total']} days ({rc['backfilled_days']} backfilled), report card (fwd {rc['horizon_days']}d):")
            for b in rc["bands"]:
                if b["n"]:
                    print(f"    {b['label']}: avg {b['avg_pct']:+.2f}% / med {b['median_pct']:+.2f}% (n={b['n']})")
                else:
                    print(f"    {b['label']}: no samples")
            base = rc["baseline"]
            print(f"    baseline: avg {base['avg_pct']:+.2f}% / med {base['median_pct']:+.2f}% (n={base['n']})")
        for f in data.get("flips", []):
            print(f"  flip {'+' if f['direction'] > 0 else '-'} {f['text']}")

    try:
        n = write_feed({f["key"]: load_history(DOCS / f["history_out"]) for f in FUNDS})
        print(f"wrote {DOCS / 'feed.xml'} ({n} verdict-change entries)")
    except Exception as exc:
        print(f"feed generation failed (non-fatal): {exc}")
