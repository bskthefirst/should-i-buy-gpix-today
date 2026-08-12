#!/usr/bin/env python3
"""Build docs/data.json for the "Should I buy GPIX today?" page.

Fetches GPIX/SPY/VIX from Yahoo Finance's public chart API and macro
headlines from Google News RSS, scores today's entry conditions with
transparent rules, and backtests dip-waiting vs plain DCA so the page
can show whether timing has actually helped.

Stdlib only - no dependencies - so it runs anywhere including GitHub Actions.
"""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "data.json"
UA = {"User-Agent": "Mozilla/5.0 (gpix-timing tool; personal use)"}

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

WEEKLY_DCA_AMOUNT = 100.0
DIP_THRESHOLD = 0.03  # dip-waiter buys only >=3% below the high so far


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def yahoo_history(symbol: str, range_: str) -> list[tuple[date, float, float]]:
    """Return (date, close, adjusted_close) rows. Adjusted close folds
    distributions back in, which is what total-return math needs."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval=1d"
    )
    data = fetch_json(url)
    result = data["chart"]["result"][0]
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


def sma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def fetch_headlines(limit: int = 6) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?"
        "q=federal+reserve+OR+inflation+OR+%22stock+market%22"
        "&hl=en-US&gl=US&ceid=US:en"
    )
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
        # Google News titles end with " - Source"
        source = ""
        m = re.search(r"\s-\s([^-]+)$", title)
        if m:
            source = m.group(1).strip()
            title = title[: m.start()].strip()
        items.append({"title": title, "source": source, "link": link})
        if len(items) >= limit:
            break
    return items


def backtest(history: list[tuple[date, float]]) -> dict:
    """Weekly $100 DCA vs saving the same $100/week in cash and only
    buying when price is >=3% below the highest close seen so far.

    Runs on ADJUSTED closes, so monthly distributions count as reinvested
    for whoever holds shares. Cash waiting for a dip earns nothing - which
    is the real cost of waiting with an 8.5%-distributing fund.
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


def build() -> dict:
    # Note: range=max silently degrades to weekly bars on Yahoo's API;
    # 3y keeps daily granularity and covers GPIX's full life (Oct 2023 launch).
    gpix = yahoo_history("GPIX", "3y")
    spy = yahoo_history("SPY", "2y")
    vix = yahoo_history("%5EVIX", "3mo")

    gpix_closes = [c for _, c, _ in gpix]
    spy_closes = [c for _, c, _ in spy]

    price = gpix_closes[-1]
    sma50 = sma(gpix_closes, 50)
    year = gpix_closes[-252:] if len(gpix_closes) >= 252 else gpix_closes
    high52 = max(year)
    drawdown = (1 - price / high52) * 100
    vs_sma50 = (price / sma50 - 1) * 100 if sma50 else 0.0

    spy_price = spy_closes[-1]
    spy_sma200 = sma(spy_closes, 200)
    spy_above_200 = spy_sma200 is not None and spy_price >= spy_sma200

    vix_level = vix[-1][1]
    gpix_total_return = [(d, a) for d, _, a in gpix]

    today = date.today()
    next_fomc = next(
        (d for d in FOMC_DATES if date.fromisoformat(d) >= today), None
    )
    days_to_fomc = (
        (date.fromisoformat(next_fomc) - today).days if next_fomc else None
    )

    # ---- transparent scoring rules ----
    signals = []

    if vix_level >= 25:
        s, note = 2, "Fear is elevated: option premiums are rich (fatter future distributions) and shares are on sale."
    elif vix_level >= 18:
        s, note = 1, "Volatility above average - premiums somewhat rich."
    elif vix_level >= 13:
        s, note = 0, "Calm market. Premiums ordinary."
    else:
        s, note = -1, "Volatility unusually low - covered-call income is thin at these levels."
    signals.append({"name": "VIX (option premium fuel)", "value": f"{vix_level:.1f}", "score": s, "note": note})

    if drawdown >= 7:
        s, note = 2, "Meaningful discount from the 52-week high."
    elif drawdown >= 3:
        s, note = 1, "Modest discount from the 52-week high."
    elif drawdown <= 0.5:
        s, note = -1, "At/near the 52-week high - you are paying full price."
    else:
        s, note = 0, "Barely below the high - no real discount."
    signals.append({"name": "GPIX vs 52-week high", "value": f"-{drawdown:.1f}%", "score": s, "note": note})

    if sma50 is None:
        s, note = 0, "Not enough history for a 50-day average."
    elif vs_sma50 < 0:
        s, note = 1, "Below its 50-day average - short-term weakness favors the buyer."
    elif vs_sma50 > 3:
        s, note = -1, "Stretched more than 3% above its 50-day average."
    else:
        s, note = 0, "Close to its 50-day average."
    signals.append({"name": "GPIX vs 50-day average", "value": f"{vs_sma50:+.1f}%", "score": s, "note": note})

    if spy_above_200:
        s, note = 0, "S&P 500 in a healthy uptrend (above its 200-day average)."
    else:
        s, note = 1, "S&P 500 below its 200-day average - bear pricing; long-term buyers historically do well adding here."
    signals.append({"name": "S&P 500 regime", "value": "uptrend" if spy_above_200 else "downtrend", "score": s, "note": note})

    fomc_this_week = days_to_fomc is not None and days_to_fomc <= 2
    signals.append({
        "name": "Fed calendar",
        "value": f"FOMC in {days_to_fomc}d" if days_to_fomc is not None else "n/a",
        "score": 0,
        "note": (
            "Rate decision within 2 days - expect a volatility spike either way. Not a reason to skip a scheduled buy."
            if fomc_this_week
            else f"Next rate decision {next_fomc}. No event risk in the next few days."
        ),
    })

    score = sum(x["score"] for x in signals)
    if score >= 3:
        verdict = {
            "tone": "good",
            "label": "Better-than-usual entry",
            "summary": "Several conditions line up in a buyer's favor today. If you have cash earmarked for GPIX, this is a sensible day to deploy it.",
        }
    elif score >= 1:
        verdict = {
            "tone": "ok",
            "label": "Mild tailwind - fine day to buy",
            "summary": "Conditions tilt slightly in your favor. Nothing dramatic, but no reason to wait either.",
        }
    elif score == 0:
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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "signals": signals,
        "gpix": {
            "price": price,
            "sma50": round(sma50, 2) if sma50 else None,
            "high52w": high52,
            "drawdown_pct": round(drawdown, 2),
            "series": [
                {"d": d.isoformat(), "c": c} for d, c, _ in gpix[-180:]
            ],
        },
        "spy": {"price": spy_price, "sma200": round(spy_sma200, 2) if spy_sma200 else None, "above_200d": spy_above_200},
        "vix": {"level": vix_level},
        "fomc": {"next": next_fomc, "days_until": days_to_fomc, "imminent": fomc_this_week},
        "backtest": backtest(gpix_total_return),
        "headlines": fetch_headlines(),
    }


if __name__ == "__main__":
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1))
    print(f"wrote {OUT}")
    print(f"verdict: {data['verdict']['label']} (score {data['verdict']['score']})")
    bt = data["backtest"]
    print(f"backtest: DCA {bt['dca_return_pct']}% vs dip-waiting {bt['dip_return_pct']}%")
