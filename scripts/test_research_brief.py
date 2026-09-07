#!/usr/bin/env python3
"""Sanity checks for research-brief freshness helpers (no network)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_data as bd  # noqa: E402


def _asset(ticker: str, tone: str, score100: int, score: float = 0.0) -> dict:
    return {
        "ticker": ticker,
        "verdict": {
            "label": f"{ticker} label",
            "summary": f"{ticker} summary.",
            "tone": tone,
            "score": score,
            "score100": score100,
            "weights_max": 8.0,
        },
        "signals": [
            {
                "name": "Short-term pullback (reversal)",
                "value": "4 down days",
                "score": score,
                "lean": "good",
            }
        ]
        if score
        else [],
        "flips": [{"direction": 1, "text": f"{ticker} example flip"}],
        "fund": {"price": 50.0, "day_change_pct": -0.5},
    }


class ResearchBriefTests(unittest.TestCase):
    def test_as_of_et_is_iso_date(self):
        value = bd.as_of_et()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}$")

    def test_write_research_brief_marks_ready_without_intl(self):
        datasets = {
            "gpix": _asset("GPIX", "neutral", 50),
            "gpiq": _asset("GPIQ", "ok", 62, score=2.0),
            "tsla": _asset("TSLA", "ok", 59, score=1.5),
            "spcx": _asset("SPCX", "neutral", 50),
            "nvda": _asset("NVDA", "ok", 59, score=1.3),
            "goog": _asset("GOOG", "neutral", 50),
        }
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            with mock.patch.object(bd, "DOCS", docs):
                brief = bd.write_research_brief(datasets)
            self.assertEqual(brief["status"], "ready")
            self.assertTrue(brief["deliverable"])
            self.assertEqual(brief["thesis_status"], "verified")
            self.assertIn("RESEARCH DESK", brief["text"])
            self.assertIn("GPIQ", brief["text"])
            self.assertIn("NVDA", brief["text"])
            self.assertIn("GOOG", brief["text"])
            self.assertNotIn("Intl", brief["text"])
            self.assertIn(f"v{bd.RULES_VERSION}", brief["text"])
            raw = json.loads((docs / "research-brief.json").read_text())
            self.assertEqual(raw["as_of_et"], brief["as_of_et"])
            self.assertIsInstance(raw["generated_at_ms"], int)
            tickers = [a["ticker"] for a in raw["assets"]]
            self.assertEqual(tickers, ["GPIX", "GPIQ", "TSLA", "SPCX", "NVDA", "GOOG"])
            self.assertTrue((docs / "research-brief.txt").exists())

    def test_openclaw_scripts_do_not_use_intl(self):
        root = Path(__file__).resolve().parent.parent / "openclaw"
        comment_block = re.compile(r"/\*[\s\S]*?\*/")
        line_comment = re.compile(r"//.*?$", re.M)
        for name in ("equity-research-build.js", "equity-research-deliver.js"):
            src = (root / name).read_text()
            code = line_comment.sub("", comment_block.sub("", src))
            self.assertNotIn("Intl", code)
            self.assertNotIn("toLocaleString", code)
            self.assertNotIn("toLocaleDateString", code)
            self.assertIn("Date.parse", src)


if __name__ == "__main__":
    unittest.main()
