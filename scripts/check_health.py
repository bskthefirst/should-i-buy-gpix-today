#!/usr/bin/env python3
"""Fail the workflow run when the data just built are degraded.

Runs AFTER the data were committed and pushed, so a bad run still ships what it
has, but the run turns red and GitHub emails the owner. Before this check a run
with a dead data source stayed green: the TSLA earnings date was missing for
seven weeks and nobody saw it.

Problems (exit 1):  a core source failed this run, or the newest price bar is
                    more than 4 days old (a long weekend is at most 4).
Notes (exit 0):     non-core sources that failed (news, earnings, FX, calendars).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DOCS = Path(__file__).resolve().parent.parent / "docs"
MAX_BAR_AGE_DAYS = 4


def main() -> int:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    problems: dict[str, set] = {}
    notes: dict[str, str] = {}
    files = sorted(DOCS.glob("data*.json"))
    if not files:
        problems["no data files"] = {"-"}
    for f in files:
        h = json.loads(f.read_text()).get("health")
        if not h:
            problems["no health block"] = problems.get("no health block", set()) | {f.name}
            continue
        for src in h.get("degraded_core", []):
            msg = f"core source failed: {src} ({h['sources'][src].get('error', 'no detail')})"
            problems.setdefault(msg, set()).add(f.name)
        for src in h.get("degraded", []):
            if src not in h.get("degraded_core", []):
                notes[src] = h["sources"][src].get("error", "no detail")
        age = (today - date.fromisoformat(h["session"]["date"])).days
        if age > MAX_BAR_AGE_DAYS:
            problems.setdefault(f"newest price bar is {h['session']['date']}, {age} days old", set()).add(f.name)

    for src, err in sorted(notes.items()):
        print(f"::warning::{src} failed: {err}")
    for msg, names in sorted(problems.items()):
        print(f"::error::{msg} [{', '.join(sorted(names))}]")
    if problems:
        print(f"health check FAILED: {len(problems)} problem(s)")
        return 1
    print(f"health check passed ({len(files)} files, {len(notes)} non-core note(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
