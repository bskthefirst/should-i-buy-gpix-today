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
        # sum of per-signal min/max scores (10 signals)
        "meter": (-7, 14),
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

# Report-card horizon: ~1 trading month.
FWD_DAYS = 21

# Short verdict-band labels (feed titles, report card, timeline tooltip).
TONE_SHORT = {
    "good": "Better-than-usual entry",
    "ok": "Mild tailwind",
    "neutral": "No edge either way",
    "rich": "No discount today",
}


# ---------------------------------------------------------------------------
# Scoring rules - the single source of truth. The live build, the historical
# backfill and the "what would flip the verdict" panel all call these, so the
# thresholds can never drift apart.
# ---------------------------------------------------------------------------

def score_vol_percentile(pct: float) -> int:
    if pct >= 90:
        return 2
    if pct >= 70:
        return 1
    if pct >= 25:
        return 0
    return -1


def score_term_structure(ratio: float) -> int:
    if ratio >= 1.00:
        return 2
    if ratio >= 0.93:
        return 1
    return 0


def score_tech_premium(pct: float) -> int:
    if pct >= 85:
        return 2
    if pct >= 65:
        return 1
    if pct >= 20:
        return 0
    return -1


def score_vrp(vrp: float) -> int:
    if vrp >= 6:
        return 1
    if vrp > -2:
        return 0
    return -1


def score_drawdown(dd: float) -> int:
    if dd >= 7:
        return 2
    if dd >= 3:
        return 1
    if dd <= 0.5:
        return -1
    return 0


def score_vs_sma50(vs: float) -> int:
    if vs < 0:
        return 1
    if vs > 3:
        return -1
    return 0


def score_regime(above_200d: bool) -> int:
    return 0 if above_200d else 1


def score_credit(oas: float, chg_1mo: float) -> int:
    if oas >= 5.0:
        return 2
    if chg_1mo >= 0.50:
        return -1
    return 0


def score_payout(advantage: float, bands: tuple) -> int:
    hi, mid = bands
    if advantage >= hi:
        return 1
    if advantage >= mid:
        return 0
    return -1


def score_fear_greed(fg: float) -> int:
    if fg <= 25:
        return 2
    if fg <= 45:
        return 1
    if fg < 75:
        return 0
    return -1


def verdict_tone(score: int) -> str:
    if score >= 5:
        return "good"
    if score >= 2:
        return "ok"
    if score >= -1:
        return "neutral"
    return "rich"


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
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def save_history(path: Path, rows: list[dict]) -> None:
    rows.sort(key=lambda r: r["date"])
    body = ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + body + "\n]\n")


def _series_or_empty(fn) -> list:
    try:
        return list(fn())
    except Exception:
        return []


def build_history(fund: dict, fund_rows: list, div_events: list, live_row: dict) -> list[dict]:
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
    # Oct 2023 inception (range=max would degrade to weekly bars).
    vol4 = _series_or_empty(lambda: cached_rows(fund["vol_symbol"], "4y"))
    vix4 = _series_or_empty(lambda: cached_rows("%5EVIX", "4y"))
    vix3m4 = _series_or_empty(lambda: cached_rows("%5EVIX3M", "4y"))
    und4 = _series_or_empty(lambda: cached_rows(fund["underlying_symbol"], "4y"))
    hy = _series_or_empty(lambda: cached_fred("BAMLH0A0HYM2"))
    tb = _series_or_empty(lambda: cached_fred("DGS3MO"))
    fg = _series_or_empty(fear_greed_history)

    vol_d = [r[0] for r in vol4]; vol_c = [r[1] for r in vol4]
    vix_d = [r[0] for r in vix4]; vix_c = [r[1] for r in vix4]
    v3_d = [r[0] for r in vix3m4]; v3_c = [r[1] for r in vix3m4]
    und_d = [r[0] for r in und4]; und_c = [r[1] for r in und4]
    hy_d = [r[0] for r in hy]; hy_v = [r[1] for r in hy]
    tb_d = [r[0] for r in tb]; tb_v = [r[1] for r in tb]
    fg_d = [r[0] for r in fg]; fg_v = [r[1] for r in fg]

    tech_ratio: list[tuple[date, float]] = []
    if fund["tech_premium"] and vix4:
        vix_by_date = {d: c for d, c, _ in vix4}
        tech_ratio = [(d, c / vix_by_date[d]) for d, c, _ in vol4 if d in vix_by_date]
    tr_d = [r[0] for r in tech_ratio]; tr_v = [r[1] for r in tech_ratio]

    def asof(dates: list, d: date) -> int:
        """Index of the latest observation on/before d, or -1."""
        return bisect.bisect_right(dates, d) - 1

    closes = [c for _, c, _ in fund_rows]

    def scores_for(i: int) -> list[tuple[str, int]]:
        d = fund_rows[i][0]
        px = closes[i]
        sc: list[tuple[str, int]] = []
        vi = asof(vol_d, d)
        if vi >= 0:
            win = vol_c[max(0, vi - 251): vi + 1]
            pct = sum(1 for c in win if c < vol_c[vi]) / len(win) * 100
            sc.append((f"{fund['vol_name']} vs its own past year", score_vol_percentile(pct)))
        a, b = asof(vix_d, d), asof(v3_d, d)
        if a >= 0 and b >= 0 and v3_c[b]:
            sc.append(("VIX term structure (1mo/3mo)", score_term_structure(vix_c[a] / v3_c[b])))
        if tech_ratio:
            ti = asof(tr_d, d)
            if ti >= 0:
                win = tr_v[max(0, ti - 251): ti + 1]
                pct = sum(1 for r in win if r < tr_v[ti]) / len(win) * 100
                sc.append(("Tech fear premium (VXN/VIX)", score_tech_premium(pct)))
        ui = asof(und_d, d)
        if ui >= 20 and vi >= 0:
            rets = [math.log(und_c[j] / und_c[j - 1]) for j in range(ui - 19, ui + 1)]
            realized = statistics.stdev(rets) * math.sqrt(252) * 100
            sc.append(("Are call-sellers overpaid?", score_vrp(vol_c[vi] - realized)))
        hi52 = max(closes[max(0, i - 251): i + 1])
        dd = round((1 - px / hi52) * 100, 1)
        sc.append(("Discount from 52-week high", score_drawdown(dd)))
        if i + 1 >= 50:
            s50 = sum(closes[i - 49: i + 1]) / 50
            sc.append((f"{fund['ticker']} vs 50-day average", score_vs_sma50(round((px / s50 - 1) * 100, 1))))
        if ui >= 199:
            s200 = sum(und_c[ui - 199: ui + 1]) / 200
            sc.append((f"{fund['underlying_name']} regime", score_regime(und_c[ui] >= s200)))
        hj = asof(hy_d, d)
        if hj >= 0:
            oas = hy_v[hj]
            sc.append(("Credit stress (junk-bond spreads)", score_credit(oas, oas - hy_v[max(0, hj - 21)])))
        tj = asof(tb_d, d)
        ttm = sum(amt for dd_, amt in div_events if d - timedelta(days=365) <= dd_ <= d)
        if tj >= 0 and ttm > 0:
            sc.append(("Payout vs safe cash", score_payout(ttm / px * 100 - tb_v[tj], fund["payout_bands"])))
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
        total = sum(s for _, s in sc)
        drivers = [
            f"{s:+d} {n}"
            for n, s in sorted((x for x in sc if x[1]), key=lambda x: -abs(x[1]))[:3]
        ]
        hist.append({
            "date": iso,
            "score": total,
            "tone": verdict_tone(total),
            "price": fund_rows[i][1],
            "adj": fund_rows[i][2],
            "backfilled": True,
            "drivers": drivers,
        })
        have.add(iso)
        changed = True
    if live_row["date"] not in have:
        hist.append(live_row)
        changed = True
    if changed or not path.exists():
        save_history(path, hist)
    else:
        hist.sort(key=lambda r: r["date"])
    return hist


def report_card(hist: list[dict]) -> dict:
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
            dict(tone=t, label=TONE_SHORT[t], **agg([v for tt, v in fwd if tt == t]))
            for t in ("good", "ok", "neutral", "rich")
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

    sub(feed, "title", "Should I buy GPIX/GPIQ today? - verdict changes")
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
        sub(entry, "title", f"{t}: {TONE_SHORT[e['frm']]} → {TONE_SHORT[e['to']]} (score {e['score']:+d})")
        sub(entry, "link", rel="alternate", href=SITE + e["fund"]["page"])
        sub(entry, "id", f"tag:bskthefirst.github.io,2026:{e['fund']['key']}:{e['date']}")
        sub(entry, "updated", f"{e['date']}T13:35:00Z")
        body = (
            f"{t} verdict moved from \"{TONE_SHORT[e['frm']]}\" to "
            f"\"{TONE_SHORT[e['to']]}\" (score {e['score']:+d})."
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

    # 1. Vol index vs its own past year (percentile of trailing daily closes)
    def sig_vol_percentile():
        below = sum(1 for c in vol_closes if c < vol_level)
        pct = below / len(vol_closes) * 100
        s = score_vol_percentile(pct)
        note = {
            2: f"{und_name} volatility in the top decile of the past year - option premiums unusually rich and shares likely marked down.",
            1: f"{und_name} volatility elevated vs the past year - premiums above their recent norm.",
            0: f"{und_name} volatility mid-range for the past year. Premiums ordinary.",
            -1: f"{und_name} volatility near the bottom of its 1-year range - the income engine is running lean.",
        }[s]
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
        s = score_term_structure(ratio)
        note = {
            2: "VIX curve inverted - genuine panic. Near-term option premiums are at their richest and shares are marked down. Historically one of the best covered-call entry setups.",
            1: "VIX curve flattening - stress building. Premiums firming up in the buyer's favor.",
            0: "Normal upward-sloping VIX curve. No unusual near-term fear.",
        }[s]
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
            s = score_tech_premium(pct)
            note = {
                2: "Tech options are at their priciest of the year relative to the broad market - fear is concentrated in the Nasdaq. GPIQ's engine gets paid extra exactly when tech is marked down.",
                1: "Tech fear running above its norm vs the broad market - Nasdaq premiums a touch richer than usual.",
                0: "Tech vol priced normally relative to the broad market.",
                -1: "Tech options unusually cheap vs the broad market - complacency in the Nasdaq, and lean premiums for GPIQ's call-selling.",
            }[s]
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
        s = score_vrp(vrp)
        note = {
            1: f"Options are pricing far more turbulence than the {und_name} is delivering - covered-call sellers like {ticker} are being overpaid right now.",
            0: "Implied and realized volatility roughly in line - the call-selling engine earns its normal keep.",
            -1: f"Realized {und_name} swings exceed what options are pricing - call selling is underpaid in this tape.",
        }[s]
        return {
            "name": "Are call-sellers overpaid?",
            "value": f"{vrp:+.1f} pts",
            "score": s,
            "note": note,
        }

    signals.append(guarded("Are call-sellers overpaid?", sig_vrp))

    # 4. Discount from 52-week high
    s = score_drawdown(drawdown)
    note = {
        2: "Meaningful discount from the 52-week high.",
        1: "Modest discount from the 52-week high.",
        -1: "At/near the 52-week high - you are paying full price.",
        0: "Barely below the high - no real discount.",
    }[s]
    signals.append({"name": "Discount from 52-week high", "value": f"{drawdown:.1f}%", "score": s, "note": note})

    # 5. Fund vs 50-day average
    if sma50 is None:
        s, note = 0, "Not enough history for a 50-day average."
    else:
        s = score_vs_sma50(vs_sma50)
        note = {
            1: "Below its 50-day average - short-term weakness favors the buyer.",
            -1: "Stretched more than 3% above its 50-day average.",
            0: "Close to its 50-day average.",
        }[s]
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
        s = score_credit(oas, chg_1mo)
        if s == 2:
            note = "Credit markets pricing serious stress. Painful headlines, but spreads this wide have historically marked strong forward returns for equity buyers."
        elif s == -1:
            note = "Credit spreads widening fast - stress building under the surface. Drawdowns often deepen after this signal; patience tends to pay."
        elif oas <= 3.5:
            note = "Credit markets calm - no stress signal in either direction."
        else:
            note = "Credit spreads moderately elevated but stable."
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
        advantage = ttm_yield - tbill
        s = score_payout(advantage, fund["payout_bands"])
        note = {
            1: f"{ticker}'s trailing payout beats riskless T-bills by a wide margin even for this fund - you are well paid for taking equity risk.",
            0: f"{ticker} yields comfortably more than cash - the normal state of affairs.",
            -1: f"T-bills pay almost as much as {ticker} with zero risk - the income case for buying today is weak.",
        }[s]
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
        s = score_fear_greed(score_)
        note = {
            2: "Extreme fear across market internals - historically the crowd is wrong at this extreme, and premiums are fat. Contrarian buy zone.",
            1: "Fear is the dominant mood - a mild contrarian tailwind for buyers.",
            0: "Sentiment in the normal range - no contrarian edge either way.",
            -1: "Extreme greed - the crowd is all-in and shares are priced for perfection.",
        }[s]
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
    tone = verdict_tone(score)
    verdict = {
        "good": {
            "label": "Better-than-usual entry",
            "summary": f"Several conditions line up in a buyer's favor today. If you have cash earmarked for {ticker}, this is a sensible day to deploy it.",
        },
        "ok": {
            "label": "Mild tailwind - fine day to buy",
            "summary": "Conditions tilt slightly in your favor. Nothing dramatic, but no reason to wait either.",
        },
        "neutral": {
            "label": "No edge either way - buy on schedule",
            "summary": "Today is average. If it's your scheduled buy day, buy. Waiting for a better day costs more than it saves, on average.",
        },
        "rich": {
            "label": "No discount today",
            "summary": "You'd be paying full price with thin premiums. If today is your scheduled buy day, buy anyway - the backtest below shows why skipping rarely helps. If it's not, there is no signal pulling you in early.",
        },
    }[tone]
    verdict["tone"] = tone
    verdict["score"] = score

    # ---- "what would flip the verdict": nearest actionable thresholds ----
    def build_flips() -> list[dict]:
        cands = []

        def add(dist, direction, label, text):
            cands.append({"dist": dist, "direction": direction, "label": label, "text": text})

        # Discount from the 52-week high
        if drawdown < 3:
            t = high52 * 0.97
            add(abs(t / price - 1) * 100, +1, "Real discount",
                f"{ticker} at ${t:.2f} ({(t / price - 1) * 100:+.1f}% from here) = real discount from the 52-week high (+1)")
        elif drawdown < 7:
            t = high52 * 0.93
            add(abs(t / price - 1) * 100, +1, "Meaningful discount",
                f"{ticker} at ${t:.2f} ({(t / price - 1) * 100:+.1f}% from here) = meaningful discount (+2)")
        if 0.5 < drawdown < 3:
            t = high52 * 0.995
            add(abs(t / price - 1) * 100, -1, "Back to full price",
                f"{ticker} back at ${t:.2f} ({(t / price - 1) * 100:+.1f}% from here) = paying full price again (\u22121)")

        # Vol index vs its trailing-year percentiles
        svol = sorted(vol_closes)
        nv = len(svol)
        vpct = sum(1 for c in vol_closes if c < vol_level) / nv * 100

        def q(p):
            return svol[min(nv - 1, int(p * nv))]

        if vpct < 70:
            lvl = q(0.70)
            add(abs(lvl / vol_level - 1) * 100, +1, "Elevated premiums",
                f"{vol_name} above {lvl:.1f} (now {vol_level:.1f}) = premiums elevated vs the past year (+1)")
        elif vpct < 90:
            lvl = q(0.90)
            add(abs(lvl / vol_level - 1) * 100, +1, "Top-decile premiums",
                f"{vol_name} above {lvl:.1f} (now {vol_level:.1f}) = top-decile premiums (+2)")
        if vpct >= 25:
            lvl = q(0.25)
            if lvl < vol_level:
                add(abs(1 - lvl / vol_level) * 100, -1, "Lean premiums",
                    f"{vol_name} below {lvl:.1f} (now {vol_level:.1f}) = income engine running lean (\u22121)")

        # VIX term structure crossing 0.93
        try:
            vix_now = list(cached_rows("%5EVIX", "1y"))[-1][1]
            vix3m = list(cached_rows("%5EVIX3M", "5d"))[-1][1]
            r = vix_now / vix3m
            if r < 0.93:
                add((0.93 / r - 1) * 100, +1, "VIX curve stress",
                    f"VIX/VIX3M ratio at 0.93 (now {r:.2f}) = stress building in the vol curve (+1)")
        except Exception:
            pass

        # Tech fear premium percentiles (GPIQ only)
        if fund["tech_premium"]:
            try:
                vix_rows = cached_rows("%5EVIX", "1y")
                vxn_rows = cached_rows("%5EVXN", "1y")
                vbd = {d: c for d, c, _ in vix_rows}
                ratios = [c / vbd[d] for d, c, _ in vxn_rows if d in vbd]
                cur = ratios[-1]
                sr = sorted(ratios)
                m = len(sr)
                tpct = sum(1 for x in ratios if x < cur) / m * 100

                def tq(p):
                    return sr[min(m - 1, int(p * m))]

                if tpct < 65:
                    add(abs(tq(0.65) / cur - 1) * 100, +1, "Tech fear premium",
                        f"VXN/VIX ratio above {tq(0.65):.2f}x (now {cur:.2f}x) = tech fear premium building (+1)")
                elif tpct < 85:
                    add(abs(tq(0.85) / cur - 1) * 100, +1, "Peak tech fear",
                        f"VXN/VIX ratio above {tq(0.85):.2f}x (now {cur:.2f}x) = tech fear at yearly extremes (+2)")
            except Exception:
                pass

        # Crossing the 50-day average
        if sma50:
            if vs_sma50 >= 0:
                add(abs(sma50 / price - 1) * 100, +1, "Below 50-day average",
                    f"{ticker} below its 50-day average of ${sma50:.2f} ({(sma50 / price - 1) * 100:+.1f}% from here) = short-term weakness (+1)")
            if 0 <= vs_sma50 <= 3:
                t = sma50 * 1.03
                add(abs(t / price - 1) * 100, -1, "Stretched",
                    f"{ticker} above ${t:.2f} (3% over its 50-day average) = stretched (\u22121)")

        # Credit-spread widening warning
        try:
            rows = cached_fred("BAMLH0A0HYM2")
            oas = rows[-1][1]
            chg = oas - rows[max(0, len(rows) - 22)][1]
            if oas < 5.0 and chg < 0.50:
                need = 0.50 - chg
                add(need / max(oas, 1.0) * 100, -1, "Credit stress warning",
                    f"Junk-bond spreads widening another {need:.2f} pts in a month (1-mo change now {chg:+.2f}) = credit stress warning (\u22121)")
        except Exception:
            pass

        # Fear & Greed thresholds
        try:
            fg_now, _ = cached_fear_greed()
            if 45 < fg_now < 75:
                add(fg_now - 45, +1, "Fear",
                    f"Fear & Greed at 45 or below (now {fg_now:.0f}) = fear, a contrarian tailwind (+1)")
                add(75 - fg_now, -1, "Extreme greed",
                    f"Fear & Greed at 75 or above (now {fg_now:.0f}) = extreme greed (\u22121)")
        except Exception:
            pass

        cands.sort(key=lambda c: c["dist"])
        picked = cands[:5]
        # Keep the panel two-sided when candidates on both sides exist.
        if picked and all(c["direction"] > 0 for c in picked):
            other = next((c for c in cands if c["direction"] < 0), None)
            if other:
                picked[-1] = other
        elif picked and all(c["direction"] < 0 for c in picked):
            other = next((c for c in cands if c["direction"] > 0), None)
            if other:
                picked[-1] = other
        picked.sort(key=lambda c: c["dist"])
        return [{"label": c["label"], "direction": c["direction"], "text": c["text"]} for c in picked]

    try:
        flips = build_flips()
    except Exception:
        flips = []

    # ---- score history: backfill missing dates + append today's live row ----
    live_drivers = [
        f"{s['score']:+d} {s['name']}"
        for s in sorted((x for x in signals if x["score"]), key=lambda x: -abs(x["score"]))[:3]
    ]
    live_row = {
        "date": fund_rows[-1][0].isoformat(),
        "score": score,
        "tone": tone,
        "price": price,
        "adj": fund_rows[-1][2],
        "backfilled": False,
        "drivers": live_drivers,
    }
    hist_path = DOCS / fund["history_out"]
    try:
        history = build_history(fund, fund_rows, div_events, live_row)
    except Exception:
        # Backfill must never break the daily build: fall back to just
        # appending today's row to whatever history already exists.
        history = load_history(hist_path)
        if live_row["date"] not in {r["date"] for r in history}:
            history.append(live_row)
            save_history(hist_path, history)
    tone_by_date = {r["date"]: r["tone"] for r in history}

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
                {"d": d.isoformat(), "c": c, "t": tone_by_date.get(d.isoformat())}
                for d, c, _ in fund_rows[-180:]
            ],
        },
        "flips": flips,
        "report_card": report_card(history) if history else None,
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
