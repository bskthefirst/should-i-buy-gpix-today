#!/usr/bin/env python3
"""Decide whether, and when, a scheduled run builds the data.

GitHub starts scheduled runs hours late (2 to 9.5 hours since 2026-08-26) and
promises no upper limit. So the cron entries in update-data.yml fire EARLY on
purpose, and this script waits until one of three New York time windows opens:

  pre-open    07:00-09:15   page is fresh before the market opens
  pre-close   15:10-15:45   provisional "buy at the close" reading
  post-close  16:25-19:00   final numbers for the day

New York time is used, so the windows stay right in both EDT and EST. Each cron
entry has a primary window (PRIMARY below). A run that starts too early waits
(at most 5.5 hours). A run that starts after its primary window falls forward to
the next window that is still ahead. Weekends do nothing. Runs started by hand
(workflow_dispatch) skip the gate and build at once.

Writes `run` (true/false) and `window` to $GITHUB_OUTPUT.
Test hooks: GATE_NOW (ISO time) replaces the clock; GATE_NO_SLEEP=1 skips the wait.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

WINDOWS = [
    ("pre-open", (7, 0), (9, 15)),
    ("pre-close", (15, 10), (15, 45)),
    ("post-close", (16, 25), (19, 0)),
]
# cron entry (as written in update-data.yml) -> its primary window
PRIMARY = {
    "7 1 * * 1-5": "pre-open",      # only matters when GitHub is 4-12 hours late
    "37 6 * * 1-5": "pre-open",
    "7 9 * * 1-5": "pre-open",
    "7 12 * * 1-5": "pre-open",
    "52 14 * * 1-5": "pre-close",
    "52 17 * * 1-5": "post-close",
}
MAX_WAIT = timedelta(hours=5, minutes=30)


def decide(now: datetime, event: str, cron: str = "") -> tuple[str | None, float]:
    """(window name or None, seconds to wait before building)."""
    if event != "schedule":
        return "manual", 0.0
    now = now.astimezone(NY)
    if now.weekday() >= 5:
        return None, 0.0
    names = [w[0] for w in WINDOWS]
    first = names.index(PRIMARY[cron]) if cron in PRIMARY else 0
    for name, (h1, m1), (h2, m2) in WINDOWS[first:]:
        start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
        if start <= now <= end:
            return name, 0.0
        if now < start:
            wait = start - now
            return (name, wait.total_seconds()) if wait <= MAX_WAIT else (None, 0.0)
        # past this window: fall forward to the next one
    return None, 0.0


def main() -> int:
    now = datetime.fromisoformat(os.environ["GATE_NOW"]) if os.environ.get("GATE_NOW") else datetime.now(NY)
    event = os.environ.get("GITHUB_EVENT_NAME", "schedule")
    cron = os.environ.get("CRON", "")
    window, wait = decide(now, event, cron)
    local = now.astimezone(NY)
    print(f"New York {local:%a %Y-%m-%d %H:%M %Z}; event={event}; cron={cron!r} -> window={window}, wait={wait / 60:.0f} min", flush=True)
    if window and wait > 0 and not os.environ.get("GATE_NO_SLEEP"):
        time.sleep(wait)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"run={'true' if window else 'false'}\nwindow={window or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
