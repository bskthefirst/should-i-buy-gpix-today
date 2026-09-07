#!/usr/bin/env python3
"""Contract tests for the external-data fetchers.

These run offline: the point is to pin the shape of each request, not to hit
the network. They exist because the earnings handshake failed silently for
weeks - guarded fetches degrade to a note on the page, so a broken one looks
identical to a quiet one, and only a test of the request itself notices.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import date, timedelta
from unittest import mock

import build_data as B

CRUMB = "abc123"


def yahoo_stub(*, fc_status: int):
    """A fake curl that mimics Yahoo's real handshake.

    fc.yahoo.com answers `fc_status` (404 in production) while still setting
    the session cookie; getcrumb and quoteSummary answer normally. curl exits
    22 on a >=400 status only when --fail is present, which is the behaviour
    that decides whether the handshake survives.
    """
    soon = (date.today() + timedelta(days=9)).isoformat()
    payload = json.dumps({"quoteSummary": {"result": [{"calendarEvents": {"earnings": {
        "earningsDate": [{"fmt": soon}], "isEarningsDateEstimate": True}}}]}})

    def run(cmd, **kwargs):
        url = cmd[-1]
        if "fc.yahoo.com" in url:
            if fc_status >= 400 and "--fail" in cmd:
                raise subprocess.CalledProcessError(22, cmd)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        if "getcrumb" in url:
            return subprocess.CompletedProcess(cmd, 0, CRUMB.encode(), b"")
        if "quoteSummary" in url:
            return subprocess.CompletedProcess(cmd, 0, payload.encode(), b"")
        raise AssertionError(f"unexpected request: {url}")

    return run, soon


class TestEarningsHandshake(unittest.TestCase):
    def setUp(self):
        B.fetch_earnings.cache_clear()

    def tearDown(self):
        B.fetch_earnings.cache_clear()

    def test_survives_the_404_from_the_cookie_hop(self):
        """The regression: fc.yahoo.com 404s by design. Running that hop under
        --fail made curl exit 22 and killed the handshake, so every stock page
        rendered 'earnings date unavailable' on every build."""
        run, soon = yahoo_stub(fc_status=404)
        with mock.patch.object(B.subprocess, "run", side_effect=run):
            got = B.fetch_earnings("NVDA")
        self.assertEqual(got, (soon, True))

    def test_still_works_if_yahoo_starts_returning_200(self):
        run, soon = yahoo_stub(fc_status=200)
        with mock.patch.object(B.subprocess, "run", side_effect=run):
            got = B.fetch_earnings("NVDA")
        self.assertEqual(got, (soon, True))

    def test_crumb_request_still_fails_loudly(self):
        """Only the cookie hop is exempt from --fail; a genuine error on the
        crumb or quoteSummary call must still raise so the caller can note it."""
        def run(cmd, **kwargs):
            if "fc.yahoo.com" in cmd[-1]:
                return subprocess.CompletedProcess(cmd, 0, b"", b"")
            self.assertIn("--fail", cmd)
            raise subprocess.CalledProcessError(22, cmd)

        with mock.patch.object(B.subprocess, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                B.fetch_earnings("NVDA")

    def test_past_earnings_dates_are_ignored(self):
        stale = (date.today() - timedelta(days=3)).isoformat()
        upcoming = (date.today() + timedelta(days=20)).isoformat()
        payload = json.dumps({"quoteSummary": {"result": [{"calendarEvents": {"earnings": {
            "earningsDate": [{"fmt": stale}, {"fmt": upcoming}]}}}]}})

        def run(cmd, **kwargs):
            url = cmd[-1]
            body = (CRUMB.encode() if "getcrumb" in url
                    else payload.encode() if "quoteSummary" in url else b"")
            return subprocess.CompletedProcess(cmd, 0, body, b"")

        with mock.patch.object(B.subprocess, "run", side_effect=run):
            self.assertEqual(B.fetch_earnings("NVDA"), (upcoming, False))


class TestChartRetry(unittest.TestCase):
    def test_retries_and_alternates_hosts(self):
        """Yahoo throttles by source IP and its two API hosts are throttled
        independently, so a 429 should pause and switch hosts rather than fail
        the build."""
        seen = []

        def flaky(url, headers=None):
            seen.append(url)
            if len(seen) < 3:
                raise OSError("HTTP Error 429: Too Many Requests")
            return {"ok": True}

        with mock.patch.object(B, "fetch_json", side_effect=flaky), \
                mock.patch.object(B.time, "sleep"):
            self.assertEqual(
                B.fetch_json_retry("https://query1.finance.yahoo.com/v8/x"), {"ok": True})
        self.assertEqual(len(seen), 3)
        self.assertIn("query2.", seen[1])

    def test_gives_up_with_a_useful_message(self):
        with mock.patch.object(B, "fetch_json", side_effect=OSError("429")), \
                mock.patch.object(B.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "giving up"):
                B.fetch_json_retry("https://query1.finance.yahoo.com/v8/x", tries=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
