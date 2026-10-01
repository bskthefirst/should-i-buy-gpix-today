#!/usr/bin/env python3
"""Invariant tests for the scoring rules and the data files they produce.

These exist because rules v4 shipped NVDA and GOOG scoring bands their own
histories did not support, and nothing in the pipeline objected. The checks
below are the ones that would have objected:

  * a ticker cannot score a band that is not in its validated `bands` tuple
  * every scored band must carry the evidence string the page prints, and
    every rejected band must carry the reason the page prints
  * the gauge axis and `weights_max` must describe scores that are actually
    reachable, so the page never advertises a band nobody can hit
  * the published JSON and history files must agree with the rules that
    supposedly produced them

Run directly (`python3 scripts/test_rules.py`) or under `python3 -m unittest`.
The data-file tests skip themselves when docs/ has not been built yet.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import build_data as B

DOCS = Path(__file__).resolve().parent.parent / "docs"
STOCKS = [f for f in B.FUNDS if f.get("kind") == "stock"]
ALL_BANDS = ("crash", "down3", "rvol_p90", "at_high", "knife")


def reachable_w(bands: tuple) -> set[float]:
    """Every weighted composite the engine can emit for a band set.

    Enumerated by driving the real scoring functions with representative
    inputs rather than by re-deriving the arithmetic, so the test fails if a
    scoring function changes without the range helper changing with it.
    """
    out = set()
    for streak, ret5 in ((0, 0.0), (3, -4.0), (5, -14.0)):
        for pct in (50.0, 95.0):
            for dd in (0.0, 5.0, 15.0, 25.0):
                # A 12%+ five-session drop cannot coexist with a price at its
                # 52-week high; skip the physically impossible combinations.
                if ret5 <= -12.0 and dd < 12.0:
                    continue
                out.add(round(B.score_stock_pullback(streak, ret5, bands)
                              + B.score_stock_rvol(pct, bands)
                              + B.score_stock_high_or_knife(dd, bands), 2))
    return out


class TestBandConfig(unittest.TestCase):
    def test_bands_are_known(self):
        for f in STOCKS:
            with self.subTest(f["ticker"]):
                self.assertLessEqual(set(f["bands"]), set(ALL_BANDS))

    def test_scored_bands_have_evidence(self):
        """Every scoring band must carry the stats string its card prints."""
        for f in STOCKS:
            for band in f["bands"]:
                with self.subTest(ticker=f["ticker"], band=band):
                    self.assertTrue(f["band_stats"].get(band),
                                    f"{f['ticker']} scores {band} with no evidence string")

    def test_rejected_bands_have_a_reason(self):
        """A band that is neither scored nor explained would render a card
        that silently omits why it doesn't count."""
        for f in STOCKS:
            if not f["bands"]:
                continue  # SPCX: the whole page is flagged too-young
            for band in ALL_BANDS:
                if band in f["bands"]:
                    continue
                with self.subTest(ticker=f["ticker"], band=band):
                    self.assertTrue(f["band_failed"].get(band),
                                    f"{f['ticker']} drops {band} with no stated reason")

    def test_no_band_scores_unless_validated(self):
        """The scoring functions must respect the band set even when the
        market conditions for an unvalidated band are met."""
        for f in STOCKS:
            bands = f["bands"]
            with self.subTest(f["ticker"]):
                if "crash" not in bands:
                    self.assertEqual(B.score_stock_pullback(0, -30.0, bands), 0.0)
                if "down3" not in bands:
                    self.assertEqual(B.score_stock_pullback(7, -1.0, bands), 0.0)
                if "rvol_p90" not in bands:
                    self.assertEqual(B.score_stock_rvol(99.0, bands), 0.0)
                if "at_high" not in bands:
                    self.assertEqual(B.score_stock_high_or_knife(0.0, bands), 0.0)
                if "knife" not in bands:
                    self.assertEqual(B.score_stock_high_or_knife(15.0, bands), 0.0)

    def test_only_tsla_has_a_negative_band(self):
        """The falling knife is the project's one negative band and it was
        validated on TSLA alone; it must not spread by copy-paste again."""
        for f in STOCKS:
            with self.subTest(f["ticker"]):
                if f["ticker"] != "TSLA":
                    self.assertNotIn("knife", f["bands"])


class TestScoreGeometry(unittest.TestCase):
    def test_score100_mapping(self):
        for w, expected in ((-1.5, 41), (0.0, 50), (1.0, 56), (2.0, 62), (3.5, 72)):
            self.assertEqual(B.score100_of(w), expected)

    def test_weight_range_matches_the_engine(self):
        for f in STOCKS:
            with self.subTest(f["ticker"]):
                seen = reachable_w(f["bands"])
                lo, hi = B.stock_weight_range(f["bands"])
                self.assertEqual((lo, hi), (min(seen), max(seen)))

    def test_crash_and_at_high_are_mutually_exclusive(self):
        """A 12% five-session drop leaves the price >=12% under its 52-week
        high, so TSLA's ceiling is 3.5 and not the 4.5 sum of its weights."""
        self.assertEqual(B.stock_weight_range(ALL_BANDS)[1], 3.5)

    def test_meter_covers_every_reachable_score(self):
        for f in STOCKS:
            with self.subTest(f["ticker"]):
                lo, hi = B.stock_meter(f["bands"])
                for w in reachable_w(f["bands"]):
                    self.assertTrue(lo <= B.score100_of(w) <= hi,
                                    f"{f['ticker']}: W={w} falls outside meter [{lo},{hi}]")

    def test_tones_are_exactly_the_reachable_ones(self):
        for f in STOCKS:
            with self.subTest(f["ticker"]):
                expected = {B.verdict_tone_stock(w) if f["bands"] else "neutral"
                            for w in reachable_w(f["bands"])}
                self.assertEqual(set(B.stock_tones(f["bands"])), expected)


class TestPublishedData(unittest.TestCase):
    """The committed docs/*.json must agree with the rules that made them."""

    def _load(self, name):
        """Read a built artifact, skipping if it predates the current rules.

        The daily workflow runs the build immediately before these tests, so a
        stale file there is impossible and nothing is skipped. Locally, right
        after a RULES_VERSION bump, docs/ still holds the previous build and
        skipping is the honest answer rather than a red suite.
        """
        path = DOCS / name
        if not path.exists():
            self.skipTest(f"{name} not built")
        data = json.loads(path.read_text())
        if data.get("rules_version") != B.RULES_VERSION:
            self.skipTest(f"{name} was built under rules v{data.get('rules_version')}, "
                          f"code is v{B.RULES_VERSION}; run build_data.py first")
        return data

    def data(self, key):
        return self._load(f"data-{key}.json")

    def history(self, key):
        return self._load(f"history-{key}.json")

    def test_verdict_matches_its_signals(self):
        for f in STOCKS:
            d = self.data(f["key"])
            with self.subTest(f["ticker"]):
                total = round(sum(s["score"] for s in d["signals"]), 2)
                self.assertEqual(total, d["verdict"]["score"])
                self.assertEqual(B.score100_of(total), d["verdict"]["score100"])
                self.assertEqual(d["verdict"]["weights_max"],
                                 B.stock_weight_range(f["bands"])[1])

    def test_no_signal_scores_outside_its_tickers_bands(self):
        for f in STOCKS:
            d = self.data(f["key"])
            allowed = set(reachable_w(f["bands"]))
            with self.subTest(f["ticker"]):
                self.assertIn(d["verdict"]["score"], allowed)

    def test_history_rows_are_internally_consistent(self):
        for f in STOCKS:
            rows = self.history(f["key"]).get("rows", [])
            allowed = set(reachable_w(f["bands"]))
            with self.subTest(f["ticker"]):
                dates = [r["date"] for r in rows]
                self.assertEqual(dates, sorted(dates))
                self.assertEqual(len(set(dates)), len(dates))
                for r in rows:
                    self.assertIn(r["score"], allowed, f"{f['ticker']} {r['date']}")
                    self.assertEqual(r["score100"], B.score100_of(r["score"]))
                    expected_tone = (B.verdict_tone_stock(r["score"])
                                     if f["bands"] else "neutral")
                    self.assertEqual(r["tone"], expected_tone)
                    self.assertAlmostEqual(
                        sum(float(x.split(" ", 1)[0]) for x in r.get("drivers", [])),
                        r["score"], places=6)

    def test_report_card_lists_only_reachable_bands(self):
        for f in STOCKS:
            d = self.data(f["key"])
            rc = d.get("report_card")
            if not rc:
                continue
            with self.subTest(f["ticker"]):
                self.assertEqual([b["tone"] for b in rc["bands"]],
                                 list(B.stock_tones(f["bands"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
