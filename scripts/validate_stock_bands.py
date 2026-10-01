#!/usr/bin/env python3
"""Test the single-stock scoring bands on one ticker at audit grade.

This is the script behind the per-ticker `bands` lists in build_data.FUNDS. It
applies the same bar the TSLA pass used, so a band only earns a weight on a
ticker where that ticker's own history supports it:

  * forward excess return vs the all-days baseline at 1, 21 and 63 trading days
  * t-stats on EFFECTIVE sample size - overlapping forward windows are not
    independent, so N is deflated by the horizon
  * four era splits, with the sign required to hold in every era at the 1-day
    horizon (the horizon where single-name reversal effects are measurable;
    longer horizons on a single stock are dominated by company drift)

Bands tested (the TSLA-validated set):

  crash     5-session return <= -12%
  down3     3+ consecutive down closes (crash days excluded, it supersedes)
  rvol_p90  20-day realized volatility >= p90 of its own trailing year
  at_high   within 0.5% of the 52-week high
  knife     10-20% below the 52-week high
  deep      35%+ below the 52-week high (context on TSLA; tested for reference)

Data comes from Nasdaq's public historical API (10 years of split-adjusted
daily closes, keyless). That is deliberately a different vendor from the Yahoo
feed the live pages use, so the validation cross-checks the site's inputs
instead of re-deriving them from the same source. Dividends are ignored: on
these names the trailing yield is a rounding error next to the effects tested,
and it shifts signal and baseline returns equally.

Usage:
    python3 scripts/validate_stock_bands.py NVDA GOOG TSLA
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_data import reversal_inputs, realized_vol_series, trailing_pct  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HORIZONS = (1, 21, 63)

# Era splits. Deliberately coarse and calendar-based rather than tuned: the
# point is that a band survives regime change, not that it survives a
# partition chosen after seeing the answer.
ERAS = [
    ("2016-2018", "2016-01-01", "2018-12-31"),
    ("2019-2021", "2019-01-01", "2021-12-31"),
    ("2022-2023", "2022-01-01", "2023-12-31"),
    ("2024-2026", "2024-01-01", "2026-12-31"),
]

# A band needs at least this many occurrences in an era before that era counts
# toward (or against) robustness, and this many overall to be testable at all.
MIN_ERA_N = 5
MIN_TOTAL_N = 30

# Lead-in before any day is scoreable: 251 sessions for the trailing-year
# window plus 20 for the realized-vol lookback.
WARMUP = 271


def fetch_closes(ticker: str, cache: Path) -> list[tuple[str, float]]:
    """10 years of split-adjusted daily closes from Nasdaq, cached on disk."""
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / f"nasdaq_{ticker}.json"
    if not (dst.exists() and dst.stat().st_size > 10_000):
        url = (f"https://api.nasdaq.com/api/quote/{ticker}/historical"
               f"?assetclass=stocks&fromdate=2005-01-01&todate={date.today()}&limit=99999")
        out = subprocess.run(
            ["curl", "-sS", "--fail", "-m", "120", "-A", UA,
             "-H", "Accept: application/json", url],
            capture_output=True, check=True).stdout
        if not json.loads(out).get("data", {}).get("tradesTable"):
            raise SystemExit(f"{ticker}: Nasdaq returned no rows")
        dst.write_bytes(out)
    rows = json.loads(dst.read_text())["data"]["tradesTable"]["rows"]
    out = []
    for r in rows:
        m, d, y = r["date"].split("/")
        out.append((f"{y}-{m}-{d}", float(r["close"].replace("$", "").replace(",", ""))))
    out.sort()
    return out


def band_membership(closes: list[float]) -> list[dict]:
    """Per-day membership in each band, using only data available that day."""
    rv = realized_vol_series(closes)
    out = []
    for i in range(len(closes)):
        streak, ret5 = reversal_inputs(closes, i)
        hi52 = max(closes[max(0, i - 251): i + 1])
        dd = round((1 - closes[i] / hi52) * 100, 1)
        pct = trailing_pct(rv, i)
        crash = ret5 is not None and ret5 <= -12.0
        out.append({
            "crash": crash,
            "down3": streak >= 3 and not crash,
            "rvol_p90": pct is not None and pct >= 90,
            "at_high": dd <= 0.5,
            "knife": 10.0 <= dd < 20.0,
            "deep": dd >= 35.0,
        })
    return out


def forward(closes: list[float], i: int, h: int) -> float | None:
    return (closes[i + h] / closes[i] - 1) * 100 if i + h < len(closes) else None


def welch_t(signal: list[float], baseline: list[float], horizon: int) -> float:
    """t on (signal mean - baseline mean), N deflated by the overlap horizon."""
    if len(signal) < 3 or len(baseline) < 3:
        return float("nan")
    ns = max(2.0, len(signal) / horizon)
    nb = max(2.0, len(baseline) / horizon)
    se = math.sqrt(statistics.variance(signal) / ns + statistics.variance(baseline) / nb)
    return (statistics.fmean(signal) - statistics.fmean(baseline)) / se if se else float("nan")


def evaluate(ticker: str, cache: Path) -> dict:
    series = fetch_closes(ticker, cache)
    dates = [d for d, _ in series]
    closes = [c for _, c in series]
    members = band_membership(closes)
    scoreable = range(WARMUP, len(closes))

    print(f"\n{'=' * 78}")
    print(f"{ticker}: {len(closes)} sessions, {dates[0]} .. {dates[-1]} "
          f"({len(closes) - WARMUP} scoreable after warm-up)")
    print("=" * 78)

    results = {}
    for band in ("crash", "down3", "rvol_p90", "at_high", "knife", "deep"):
        rec = {"horizons": {}, "eras": {}}
        print(f"\n  [{band}]")
        for h in HORIZONS:
            base = [v for i in scoreable for v in [forward(closes, i, h)] if v is not None]
            hits = [v for i in scoreable if members[i][band]
                    for v in [forward(closes, i, h)] if v is not None]
            if len(hits) < MIN_TOTAL_N:
                print(f"    {h:>2}d: n={len(hits):4d} - below the {MIN_TOTAL_N}-day "
                      f"testability floor")
                continue
            excess = statistics.fmean(hits) - statistics.fmean(base)
            t = welch_t(hits, base, h)
            rec["horizons"][h] = {"n": len(hits), "excess_pt": round(excess, 3),
                                  "t": round(t, 2)}
            print(f"    {h:>2}d: n={len(hits):4d}  signal {statistics.fmean(hits):+6.2f}%  "
                  f"baseline {statistics.fmean(base):+6.2f}%  "
                  f"excess {excess:+6.2f}pt  t={t:+5.2f}")
        for h in HORIZONS:
            cells, signs = [], []
            for name, lo, hi in ERAS:
                base = [v for i in scoreable if lo <= dates[i] <= hi
                        for v in [forward(closes, i, h)] if v is not None]
                hits = [v for i in scoreable if members[i][band] and lo <= dates[i] <= hi
                        for v in [forward(closes, i, h)] if v is not None]
                if len(hits) < MIN_ERA_N or not base:
                    cells.append(f"{name}: n/a")
                    signs.append(None)
                    continue
                e = statistics.fmean(hits) - statistics.fmean(base)
                cells.append(f"{name}: {e:+.2f}pt (n={len(hits)})")
                signs.append(round(e, 3))
            rec["eras"][h] = signs
            if rec["horizons"].get(h):
                print(f"      eras @{h}d -> " + " | ".join(cells))
        results[band] = rec
    return results


def robust(rec: dict, want: int, horizon: int = 1) -> str:
    """The pass/fail call: right sign overall AND in every testable era."""
    h = rec["horizons"].get(horizon)
    if not h:
        return "untestable"
    signs = [s for s in rec["eras"].get(horizon, []) if s is not None]
    if len(signs) < 2:
        return "untestable"
    right = (h["excess_pt"] > 0) == (want > 0)
    all_era = all((s > 0) == (want > 0) for s in signs)
    if right and all_era:
        return "PASS"
    return "FAIL (wrong sign)" if not right else "FAIL (era-mixed)"


def main(tickers: list[str]) -> None:
    cache = Path("/tmp/stock_validation")
    # Signs the TSLA pass found, i.e. what the framework assumes when it is
    # transplanted onto another ticker.
    want = {"crash": +1, "down3": +1, "rvol_p90": +1, "at_high": +1, "knife": -1}
    table = {t: evaluate(t, cache) for t in tickers}

    print(f"\n{'=' * 78}")
    print("DOES EACH TSLA-VALIDATED BAND HOLD ON THIS TICKER?")
    print("(1-day horizon, sign required in every era with >= "
          f"{MIN_ERA_N} occurrences)")
    print("=" * 78)
    header = f"\n  {'band':<10}{'TSLA sign':<12}" + "".join(f"{t:>26}" for t in tickers)
    print(header)
    for band, sign in want.items():
        cells = []
        for t in tickers:
            rec = table[t][band]
            h = rec["horizons"].get(1)
            excess = f"{h['excess_pt']:+.2f}pt " if h else ""
            cells.append(f"{excess}{robust(rec, sign)}")
        print(f"  {band:<10}{('positive' if sign > 0 else 'NEGATIVE'):<12}"
              + "".join(f"{c:>26}" for c in cells))
    print("\n  Bands that PASS earn their TSLA weight on that ticker; everything")
    print("  else renders as context with score 0, exactly as the fund audits do.")


if __name__ == "__main__":
    main(sys.argv[1:] or ["TSLA", "NVDA", "GOOG"])
