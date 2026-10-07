import copy
import importlib.util
import unittest
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
from datetime import datetime, timezone, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location('digest', Path(__file__).with_name('retirement_digest.py'))
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.plan = digest.validate_plan({'schemaVersion': 1, 'sharesGpix': 56.71, 'sharesGpiq': 3, 'taxPct': 15, 'target': 250, 'costs': []})
        self.feeds = {t: {'ticker': t, 'generated_at': self.now.isoformat(), 'fund': {'price': 50}, 'distributions': {'last_amount': .4, 'ttm_sum': 4.8}}
                      for t in ('GPIX', 'GPIQ')}

    def test_exact_shares_and_single_tax_deduction(self):
        result = digest.calculate(self.plan, self.feeds, self.now)
        self.assertAlmostEqual(result['latest'], 59.71 * .4 * .85)
        self.assertAlmostEqual(result['average'], result['latest'])
        self.assertEqual(result['next']['threshold'], 25)
        self.assertEqual(len(result['reached']), 1)
        self.plan['taxPct'] = 15.4
        self.assertAlmostEqual(digest.calculate(self.plan, self.feeds, self.now)['latest'], 59.71 * .4 * .846)

    def test_missing_payout_and_stale_data_stop_the_estimate(self):
        for patch in ({'distributions': {'last_amount': None, 'ttm_sum': 4.8}}, {'generated_at': (self.now - timedelta(days=5)).isoformat()}):
            feeds = copy.deepcopy(self.feeds)
            feeds['GPIX'].update(patch)
            with self.assertRaises((ValueError, TypeError)):
                digest.calculate(self.plan, feeds, self.now)

    def test_zero_holding_does_not_require_a_payout(self):
        self.plan['sharesGpiq'] = 0
        self.feeds['GPIQ']['distributions'] = {}
        self.assertGreater(digest.calculate(self.plan, self.feeds, self.now)['latest'], 0)

    def test_invalid_profile_does_not_create_income(self):
        for value in (-1, float('nan'), True, '56'):
            with self.assertRaises(ValueError):
                digest.validate_plan({**self.plan, 'schemaVersion': 1, 'sharesGpix': value})

    def test_matching_expense_and_goal_milestones(self):
        self.plan['target'] = 30
        self.plan['costs'] = [{'name': 'Coffee', 'amount': 5, 'times': 1, 'per': 'week'}]
        rows = digest.milestones(self.plan)
        self.assertEqual(len(rows), 10)
        self.assertEqual(sum(abs(r['threshold'] - 5 * 52 / 12) < 1e-8 for r in rows), 2)
        self.assertTrue(any(r['threshold'] == 30 for r in rows))

    def test_milestone_messages_start_silently_and_do_not_repeat(self):
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            plan_file = directory / 'plan.json'
            state = directory / 'state.json'
            raw = {**self.plan, 'schemaVersion': 1, 'sharesGpix': 0, 'sharesGpiq': 0}
            plan_file.write_text(json.dumps(raw))
            for ticker, filename in [('GPIX', 'data.json'), ('GPIQ', 'data-gpiq.json')]:
                feed = {**self.feeds[ticker], 'generated_at': datetime.now(timezone.utc).isoformat()}
                (directory / filename).write_text(json.dumps(feed))
            command = [sys.executable, str(Path(__file__).with_name('retirement_digest.py')), '--plan', str(plan_file), '--data-dir', str(directory), '--milestones-only', '--state', str(state)]
            def run():
                return subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip()
            self.assertEqual(run(), 'NO_REPLY')
            raw['sharesGpix'] = 100
            plan_file.write_text(json.dumps(raw))
            self.assertIn('Your dividend progress', run())
            self.assertEqual(run(), 'NO_REPLY')
            raw['sharesGpix'] = 0
            plan_file.write_text(json.dumps(raw))
            self.assertEqual(run(), 'NO_REPLY')
            raw['sharesGpix'] = 100
            plan_file.write_text(json.dumps(raw))
            self.assertEqual(run(), 'NO_REPLY')


if __name__ == '__main__':
    unittest.main()
