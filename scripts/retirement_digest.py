#!/usr/bin/env python3
"""Print an after-tax income digest for OpenClaw's Telegram announcement jobs.

The private --plan file is exported from retire.html. No Telegram credentials,
share counts, or purchase prices are published in this repository.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

PUBLIC_BASE = "https://bskthefirst.github.io/should-i-buy-gpix-today/"
LEVELS = (10, 25, 50, 100, 250, 500, 1000)


def number(value, name, maximum=1e8):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


def validate_plan(raw):
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ValueError("Export a current holdings file from the retirement page")
    plan = {name: number(raw.get(name), name) for name in ("sharesGpix", "sharesGpiq")}
    plan["taxPct"] = number(raw.get("taxPct"), "tax rate", 60)
    plan["target"] = number(raw.get("target"), "income goal", 1e6)
    if plan["target"] <= 0:
        raise ValueError("The income goal must be positive")
    costs = raw.get("costs", [])
    if not isinstance(costs, list) or len(costs) > 12:
        raise ValueError("Invalid expense list")
    plan["costs"] = []
    for row in costs:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or row.get("per") not in ("month", "week"):
            raise ValueError("Invalid expense")
        plan["costs"].append({"name": " ".join(row["name"].split())[:30], "amount": number(row.get("amount"), "expense amount", 1e6),
                              "times": number(row.get("times"), "expense count", 100), "per": row["per"]})
    return plan


def milestones(plan):
    rows = [(v, f"${v:,} a month") for v in LEVELS]
    if plan["target"] not in LEVELS:
        rows.append((plan["target"], f"Your goal: ${plan['target']:,.2f} a month"))
    costs = sorted([(r, r["amount"] * r["times"] * (52 / 12 if r["per"] == "week" else 1))
                    for r in plan["costs"] if r["name"] and r["amount"] * r["times"] > 0], key=lambda v: v[1])
    running = 0
    for i, (row, amount) in enumerate(costs):
        running += amount
        title = (row["name"] + " paid" if i == 0 else costs[0][0]["name"] + " and " + row["name"] + " paid" if i == 1
                 else "All " + str(len(costs)) + " costs paid" if i == len(costs) - 1 else str(i + 1) + " costs paid")
        rows.append((running, title))
        if row["per"] == "week":
            name = row["name"].lower()
            for count in range(1, min(7, math.floor(row["times"])) + 1):
                plural = name if count == 1 else name + ("es" if name.endswith(("ch", "sh", "s", "x", "z")) else "s")
                rows.append((count * row["amount"] * 52 / 12, f"{count} free {plural} a week"))
    return [{"threshold": value, "title": title, "id": hashlib.sha256(f"{value:.8f}:{title}".encode()).hexdigest()[:16]}
            for value, title in sorted(rows, key=lambda r: r[0])]


def calculate(plan, feeds, now=None):
    now = now or datetime.now(timezone.utc)
    latest = annual = value = 0
    dates = []
    for key, ticker in (("Gpix", "GPIX"), ("Gpiq", "GPIQ")):
        data = feeds[ticker]
        if data.get("ticker") != ticker:
            raise ValueError(f"The {ticker} feed has the wrong ticker")
        generated = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None or (now - generated).total_seconds() > 4 * 86400 or (generated - now).total_seconds() > 3600:
            raise ValueError(f"The {ticker} market data is stale or has an invalid timestamp")
        dates.append(generated)
        shares = plan["shares" + key]
        price = number(data.get("fund", {}).get("price"), f"{ticker} price")
        value += shares * price
        if shares == 0:
            continue
        dist = data.get("distributions") or {}
        latest += shares * number(dist.get("last_amount"), f"{ticker} latest payout")
        annual += shares * number(dist.get("ttm_sum"), f"{ticker} past-year payouts")
    factor = 1 - plan["taxPct"] / 100
    average = annual * factor / 12
    rows = milestones(plan)
    reached = [r for r in rows if average >= r["threshold"] - 1e-9]
    next_row = next((r for r in rows if average < r["threshold"] - 1e-9), None)
    return {"latest": latest * factor, "annual": annual * factor, "average": average, "value": value,
            "reached": reached, "next": next_row, "as_of": min(dates).strftime("%Y-%m-%d %H:%M UTC")}


def digest(plan, result):
    lines = ["Your dividend progress", f"Latest monthly payout estimate: ${result['latest']:,.2f} after {plan['taxPct']:g}% tax",
             f"Past-year average: ${result['average']:,.2f}/month after tax", f"Income goal: ${plan['target']:,.2f}/month",
             f"Income badges reached: {len(result['reached'])} of {len(milestones(plan))}"]
    if result["next"]:
        row = result["next"]
        gap = max(.01, math.ceil((row["threshold"] - result["average"]) * 100 - 1e-9) / 100)
        lines.append(f"Next: {row['title']} — ${gap:,.2f}/month to go")
    else:
        lines.append("All current income milestones reached.")
    lines.extend([f"Data: {result['as_of']}", "Based on your saved share counts. Future payouts can change.", PUBLIC_BASE + "retire.html"])
    return "\n".join(lines)


def fetch_data(ticker):
    filename = "data.json" if ticker == "GPIX" else "data-gpiq.json"
    request = Request(PUBLIC_BASE + filename, headers={"User-Agent": "retirement-digest/1.0", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, help="Use downloaded snapshots instead of the website")
    parser.add_argument("--milestones-only", action="store_true", help="Print NO_REPLY until a previously unreached estimated income milestone is reached")
    parser.add_argument("--state", type=Path, help="Private milestone record; required with --milestones-only")
    args = parser.parse_args()
    if args.milestones_only and not args.state:
        parser.error("--milestones-only requires --state")
    try:
        plan = validate_plan(json.loads(args.plan.read_text()))
        feeds = {t: json.loads((args.data_dir / ("data.json" if t == "GPIX" else "data-gpiq.json")).read_text()) if args.data_dir else fetch_data(t)
                 for t in ("GPIX", "GPIQ")}
        result = calculate(plan, feeds)
        if args.milestones_only:
            old = json.loads(args.state.read_text()) if args.state.exists() else None
            seen = set(old["reached"]) if old else set()
            reached = {r["id"] for r in result["reached"]}
            new = reached - seen
            args.state.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.state.with_name(args.state.name + ".tmp")
            temporary.write_text(json.dumps({"reached": sorted(seen | reached)}))
            temporary.chmod(0o600)
            temporary.replace(args.state)
            # First run sets a baseline, so it cannot flood the chat with old badges.
            if old is None or not new:
                print("NO_REPLY")
                return
        print(digest(plan, result))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"No dividend estimate sent: {exc}\n")


if __name__ == "__main__":
    main()
