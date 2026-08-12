#!/usr/bin/env python3
"""Build docs/data.json (GPIX) and docs/data-gpiq.json (GPIQ).

Fetches fund/underlying/vol-index history from Yahoo Finance's public
chart API, credit spreads and T-bill rates from FRED, CNN's Fear & Greed
index, and macro headlines from Google News RSS. Scores today's entry
conditions with transparent rules and backtests dip-waiting vs plain DCA
so each page can show whether timing has actually helped.

GPIX is scored against the S&P 500 and the VIX; GPIQ against the
Nasdaq-100 (QQQ) and the VXN, plus one extra Nasdaq-only signal: the
VXN/VIX "tech fear premium" (how rich tech options are vs the broad
market, ranked against its own past year).

Every non-core fetch is individually guarded: if a source is down, its
signal is emitted with score 0 and a "skipped" note instead of failing
the build.

Stdlib only - no dependencies - so it runs anywhere including GitHub Actions.
"""

from __future__ import annotations

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
        "underlying_symbol": "SPY",
        "underlying_name": "S&P 500",
        "vol_symbol": "%5EVIX",
        "vol_name": "VIX",
        "tech_premium": False,
        # GPIX trails ~8%: beating cash by 5.5+ pts is genuinely wide.
        "payout_bands": (5.5, 3.0),
        "news_query": 'federal+reserve+OR+inflation+OR+%22stock+market%22',
        # sum of per-signal min/max scores (10 signals)
        "meter": (-7, 14),
    },
    {
        "key": "gpiq",
        "ticker": "GPIQ",
        "out": "data-gpiq.json",
        "underlying_symbol": "QQQ",
        "underlying_name": "Nasdaq-100",
        "vol_symbol": "%5EVXN",
        "vol_name": "VXN",
        "tech_premium": True,
        # GPIQ trails ~10%: demand a wider margin over cash before calling
        # it a tailwind, so the page doesn't award a permanent free point.
        "payout_bands": (7.5, 4.5),
        "news_query": 'Nasdaq+OR+%22tech+stocks%22+OR+%22Federal+Reserve%22+OR+AI',
        # sum of per-signal min/max scores (11 signals incl. tech premium)
        "meter": (-8, 16),
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
    """FRED CSV: header 'observation_date,SERIES_ID'; missing values are '.'."""
    start = (date.today() - timedelta(days=100)).isoformat()
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
def cached_fear_greed() -> tuple[float, str]:
    data = json.loads(
        curl_fetch(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=CNN_HEADERS,
        )
    )
    return float(data["fear_and_greed"]["score"]), str(data["fear_and_greed"]["rating"])


def sma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


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


def guarded(name: str, builder):
    """Run a signal builder; on any failure emit a neutral placeholder so
    one flaky endpoint never breaks the daily build."""
    try:
        return builder()
    except Exception:
        return {"name": name, "value": "n/a", "score": 0, "note": SKIPPED_NOTE}


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
    und = list(cached_rows(fund["underlying_symbol"], "2y"))
    vol = list(cached_rows(fund["vol_symbol"], "1y"))

    fund_closes = [c for _, c, _ in fund_rows]
    und_closes = [c for _, c, _ in und]
    vol_closes = [c for _, c, _ in vol]

    price = fund_closes[-1]
    day_change = (price / fund_closes[-2] - 1) * 100 if len(fund_closes) > 1 else 0.0
    sma50 = sma(fund_closes, 50)
    year = fund_closes[-252:] if len(fund_closes) >= 252 else fund_closes
    high52 = max(year)
    # Round once and reuse everywhere so the page never shows two versions
    # of the same number.
    drawdown = round((1 - price / high52) * 100, 1)
    vs_sma50 = round((price / sma50 - 1) * 100, 1) if sma50 else 0.0

    und_price = und_closes[-1]
    und_sma200 = sma(und_closes, 200)
    und_above_200 = und_sma200 is not None and und_price >= und_sma200

    vol_level = vol_closes[-1]

    today = date.today()

    # ---- income data (used by a signal and shown on the page) ----
    ttm_yield = None
    tbill = None
    try:
        divs = fund_result.get("events", {}).get("dividends", {})
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        ttm_sum = sum(
            d["amount"]
            for d in divs.values()
            if datetime.fromtimestamp(d["date"], tz=timezone.utc) >= cutoff
        )
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

    # 1. Vol index vs its own past year (percentile of trailing daily closes)
    def sig_vol_percentile():
        below = sum(1 for c in vol_closes if c < vol_level)
        pct = below / len(vol_closes) * 100
        if pct >= 90:
            s, note = 2, f"{und_name} volatility in the top decile of the past year - option premiums unusually rich and shares likely marked down."
        elif pct >= 70:
            s, note = 1, f"{und_name} volatility elevated vs the past year - premiums above their recent norm."
        elif pct >= 25:
            s, note = 0, f"{und_name} volatility mid-range for the past year. Premiums ordinary."
        else:
            s, note = -1, f"{und_name} volatility near the bottom of its 1-year range - the income engine is running lean."
        return {
            "name": f"{vol_name} vs its own past year",
            "value": f"{vol_level:.1f} (p{pct:.0f})",
            "score": s,
            "note": note,
        }

    signals.append(guarded(f"{vol_name} vs its own past year", sig_vol_percentile))

    # 2. VIX term structure (VIX / VIX3M) - broad-market vol curve;
    # relevant to both funds because panics are market-wide events.
    def sig_term_structure():
        vix_now = list(cached_rows("%5EVIX", "1y"))[-1][1]
        vix3m = list(cached_rows("%5EVIX3M", "5d"))[-1][1]
        ratio = vix_now / vix3m
        if ratio >= 1.00:
            s, note = 2, "VIX curve inverted - genuine panic. Near-term option premiums are at their richest and shares are marked down. Historically one of the best covered-call entry setups."
        elif ratio >= 0.93:
            s, note = 1, "VIX curve flattening - stress building. Premiums firming up in the buyer's favor."
        else:
            s, note = 0, "Normal upward-sloping VIX curve. No unusual near-term fear."
        return {
            "name": "VIX term structure (1mo/3mo)",
            "value": f"{ratio:.2f}",
            "score": s,
            "note": note,
        }

    signals.append(guarded("VIX term structure (1mo/3mo)", sig_term_structure))

    # 2b. GPIQ only: tech fear premium - VXN/VIX ratio ranked against its
    # own past year. When tech options are unusually expensive relative to
    # broad-market options, the Nasdaq call-selling engine is being paid
    # extra, and tech shares are usually already marked down.
    if fund["tech_premium"]:
        def sig_tech_premium():
            vix_rows = cached_rows("%5EVIX", "1y")
            vxn_rows = cached_rows("%5EVXN", "1y")
            vix_by_date = {d: c for d, c, _ in vix_rows}
            ratios = [c / vix_by_date[d] for d, c, _ in vxn_rows if d in vix_by_date]
            cur = ratios[-1]
            below = sum(1 for r in ratios if r < cur)
            pct = below / len(ratios) * 100
            if pct >= 85:
                s, note = 2, "Tech options are at their priciest of the year relative to the broad market - fear is concentrated in the Nasdaq. GPIQ's engine gets paid extra exactly when tech is marked down."
            elif pct >= 65:
                s, note = 1, "Tech fear running above its norm vs the broad market - Nasdaq premiums a touch richer than usual."
            elif pct >= 20:
                s, note = 0, "Tech vol priced normally relative to the broad market."
            else:
                s, note = -1, "Tech options unusually cheap vs the broad market - complacency in the Nasdaq, and lean premiums for GPIQ's call-selling."
            return {
                "name": "Tech fear premium (VXN/VIX)",
                "value": f"{cur:.2f}x (p{pct:.0f})",
                "score": s,
                "note": note,
            }

        signals.append(guarded("Tech fear premium (VXN/VIX)", sig_tech_premium))

    # 3. Variance risk premium: implied vol minus underlying's realized vol
    def sig_vrp():
        rets = [
            math.log(und_closes[i] / und_closes[i - 1])
            for i in range(len(und_closes) - 20, len(und_closes))
        ]
        realized = statistics.stdev(rets) * math.sqrt(252) * 100
        vrp = vol_level - realized
        if vrp >= 6:
            s, note = 1, f"Options are pricing far more turbulence than the {und_name} is delivering - covered-call sellers like {ticker} are being overpaid right now."
        elif vrp > -2:
            s, note = 0, "Implied and realized volatility roughly in line - the call-selling engine earns its normal keep."
        else:
            s, note = -1, f"Realized {und_name} swings exceed what options are pricing - call selling is underpaid in this tape."
        return {
            "name": "Are call-sellers overpaid?",
            "value": f"{vrp:+.1f} pts",
            "score": s,
            "note": note,
        }

    signals.append(guarded("Are call-sellers overpaid?", sig_vrp))

    # 4. Discount from 52-week high
    if drawdown >= 7:
        s, note = 2, "Meaningful discount from the 52-week high."
    elif drawdown >= 3:
        s, note = 1, "Modest discount from the 52-week high."
    elif drawdown <= 0.5:
        s, note = -1, "At/near the 52-week high - you are paying full price."
    else:
        s, note = 0, "Barely below the high - no real discount."
    signals.append({"name": "Discount from 52-week high", "value": f"{drawdown:.1f}%", "score": s, "note": note})

    # 5. Fund vs 50-day average
    if sma50 is None:
        s, note = 0, "Not enough history for a 50-day average."
    elif vs_sma50 < 0:
        s, note = 1, "Below its 50-day average - short-term weakness favors the buyer."
    elif vs_sma50 > 3:
        s, note = -1, "Stretched more than 3% above its 50-day average."
    else:
        s, note = 0, "Close to its 50-day average."
    signals.append({"name": f"{ticker} vs 50-day average", "value": f"{vs_sma50:+.1f}%", "score": s, "note": note})

    # 6. Underlying index regime
    if und_above_200:
        s, note = 0, f"{und_name} in a healthy uptrend (above its 200-day average)."
    else:
        s, note = 1, f"{und_name} below its 200-day average - bear pricing; long-term buyers historically do well adding here."
    signals.append({"name": f"{und_name} regime", "value": "uptrend" if und_above_200 else "downtrend", "score": s, "note": note})

    # 7. High-yield credit spread (FRED BAMLH0A0HYM2)
    def sig_credit():
        rows = cached_fred("BAMLH0A0HYM2")
        oas = rows[-1][1]
        prior = rows[max(0, len(rows) - 22)][1]
        chg_1mo = oas - prior
        if oas >= 5.0:
            s, note = 2, "Credit markets pricing serious stress. Painful headlines, but spreads this wide have historically marked strong forward returns for equity buyers."
        elif chg_1mo >= 0.50:
            s, note = -1, "Credit spreads widening fast - stress building under the surface. Drawdowns often deepen after this signal; patience tends to pay."
        elif oas <= 3.5:
            s, note = 0, "Credit markets calm - no stress signal in either direction."
        else:
            s, note = 0, "Credit spreads moderately elevated but stable."
        return {
            "name": "Credit stress (junk-bond spreads)",
            "value": f"{oas:.2f}% ({chg_1mo:+.2f} 1mo)",
            "score": s,
            "note": note,
        }

    signals.append(guarded("Credit stress (junk-bond spreads)", sig_credit))

    # 8. Trailing payout vs riskless cash (bands are fund-specific:
    # GPIQ's headline yield is structurally higher, so it must clear a
    # higher bar before that counts as a tailwind)
    def sig_payout():
        if ttm_yield is None or tbill is None:
            raise ValueError("missing yield inputs")
        hi, mid = fund["payout_bands"]
        advantage = ttm_yield - tbill
        if advantage >= hi:
            s, note = 1, f"{ticker}'s trailing payout beats riskless T-bills by a wide margin even for this fund - you are well paid for taking equity risk."
        elif advantage >= mid:
            s, note = 0, f"{ticker} yields comfortably more than cash - the normal state of affairs."
        else:
            s, note = -1, f"T-bills pay almost as much as {ticker} with zero risk - the income case for buying today is weak."
        return {
            "name": "Payout vs safe cash",
            "value": f"{ttm_yield:.1f}% vs {tbill:.1f}% cash",
            "score": s,
            "note": note,
        }

    signals.append(guarded("Payout vs safe cash", sig_payout))

    # 9. CNN Fear & Greed (contrarian)
    def sig_fear_greed():
        score_, rating = cached_fear_greed()
        if score_ <= 25:
            s, note = 2, "Extreme fear across market internals - historically the crowd is wrong at this extreme, and premiums are fat. Contrarian buy zone."
        elif score_ <= 45:
            s, note = 1, "Fear is the dominant mood - a mild contrarian tailwind for buyers."
        elif score_ < 75:
            s, note = 0, "Sentiment in the normal range - no contrarian edge either way."
        else:
            s, note = -1, "Extreme greed - the crowd is all-in and shares are priced for perfection."
        return {
            "name": "Fear & Greed index",
            "value": f"{score_:.0f} ({rating})",
            "score": s,
            "note": note,
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
        ev_note = "CPI print within 2 days - the biggest single-day volatility event most months. Not a reason to skip a scheduled buy, but don't be surprised by a swing."
    elif imminent:
        ev_note = "Rate decision within 2 days - expect a volatility spike either way. Not a reason to skip a scheduled buy."
    else:
        parts = []
        if next_cpi:
            parts.append(f"next CPI {next_cpi}")
        if next_fomc:
            parts.append(f"next FOMC {next_fomc}")
        ev_note = "No event risk in the next few days" + (" (" + ", ".join(parts) + ")." if parts else ".")
    ev_value = f"{events[0][1]} in {events[0][0]}d" if events else "n/a"
    signals.append({"name": "Event calendar", "value": ev_value, "score": 0, "note": ev_note})

    # ---- verdict ----
    score = sum(x["score"] for x in signals)
    if score >= 5:
        verdict = {
            "tone": "good",
            "label": "Better-than-usual entry",
            "summary": f"Several conditions line up in a buyer's favor today. If you have cash earmarked for {ticker}, this is a sensible day to deploy it.",
        }
    elif score >= 2:
        verdict = {
            "tone": "ok",
            "label": "Mild tailwind - fine day to buy",
            "summary": "Conditions tilt slightly in your favor. Nothing dramatic, but no reason to wait either.",
        }
    elif score >= -1:
        verdict = {
            "tone": "neutral",
            "label": "No edge either way - buy on schedule",
            "summary": "Today is average. If it's your scheduled buy day, buy. Waiting for a better day costs more than it saves, on average.",
        }
    else:
        verdict = {
            "tone": "rich",
            "label": "No discount today",
            "summary": "You'd be paying full price with thin premiums. If today is your scheduled buy day, buy anyway - the backtest below shows why skipping rarely helps. If it's not, there is no signal pulling you in early.",
        }
    verdict["score"] = score

    meter_min, meter_max = fund["meter"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": ticker,
        "verdict": verdict,
        "meter": {"min": meter_min, "max": meter_max},
        "signals": signals,
        "fund": {
            "price": price,
            "day_change_pct": round(day_change, 2),
            "sma50": round(sma50, 2) if sma50 else None,
            "high52w": high52,
            "drawdown_pct": drawdown,
            "series": [
                {"d": d.isoformat(), "c": c} for d, c, _ in fund_rows[-180:]
            ],
        },
        "income": {"ttm_yield_pct": ttm_yield, "tbill_3mo": tbill},
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


if __name__ == "__main__":
    DOCS.mkdir(parents=True, exist_ok=True)
    for fund in FUNDS:
        data = build(fund)
        out = DOCS / fund["out"]
        out.write_text(json.dumps(data, indent=1))
        print(f"wrote {out}")
        print(f"{fund['ticker']} verdict: {data['verdict']['label']} (score {data['verdict']['score']})")
        for s in data["signals"]:
            print(f"  {s['score']:+d}  {s['name']}: {s['value']}")
        bt = data["backtest"]
        print(f"  backtest: DCA {bt['dca_return_pct']}% vs dip-waiting {bt['dip_return_pct']}%")
