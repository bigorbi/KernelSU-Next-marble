import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ci


class GateTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'historical_susfs_sha': 'a'*40, 'historical_susfs_evidence': 'verified original-run record',
                       'lifetime_review_complete': True, 'lifetime_review_evidence': 'reviewed corrective source/test evidence'}
        self.source = {'susfs_sha': 'a'*40}
        self.config = {'abi_options_match': True, 'release_match': True}

    def evaluate(self, exploratory='false', checks=True, reference=True):
        with patch.object(ci, 'policy', return_value=self.policy), patch.dict(os.environ, {'CAMERA_EXPLORATORY': exploratory}):
            return ci.gate(checks, self.source, self.config, reference)

    def test_exploratory_never_passes(self):
        passed, reasons = self.evaluate('true')
        self.assertFalse(passed)
        self.assertTrue(any('Exploratory' in reason for reason in reasons))

    def test_unresolved_historical_revision(self):
        self.policy['historical_susfs_sha'] = None
        self.assertFalse(self.evaluate()[0])

    def test_wrong_selected_revision(self):
        self.source['susfs_sha'] = 'b'*40
        self.assertFalse(self.evaluate()[0])

    def test_lifetime_review_required(self):
        self.policy['lifetime_review_complete'] = False
        self.assertFalse(self.evaluate()[0])

    def test_abi_failure_blocks(self):
        self.assertFalse(self.evaluate(checks=False)[0])

    def test_config_release_and_reference_required(self):
        self.assertFalse(self.evaluate(reference=False)[0])
        self.config['release_match'] = False
        self.assertFalse(self.evaluate()[0])

    def test_all_checks_required_for_pass(self):
        self.assertEqual(self.evaluate(), (True, []))

    def test_real_stages_reject_local_execution(self):
        env = dict(os.environ)
        env.pop('GITHUB_ACTIONS', None)
        result = subprocess.run([sys.executable, str(ROOT / 'ci.py'), 'kernel'], capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('only on GitHub Actions', result.stderr)


if __name__ == '__main__':
    unittest.main()
