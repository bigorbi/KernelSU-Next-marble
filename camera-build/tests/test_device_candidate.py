import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import device_candidate as candidate

ROOT = Path(__file__).resolve().parents[1]


class DeviceCandidateTests(unittest.TestCase):
    def test_only_two_binary_equivalence_checks_are_diagnostic(self):
        checks = {name: True for name in candidate.BASELINE_REQUIRED}
        checks.update(allocatable_sections=False, compiled_source_equivalence=False)
        self.assertTrue(candidate.baseline_eligible(checks))
        for name in candidate.BASELINE_REQUIRED:
            with self.subTest(name=name):
                self.assertFalse(candidate.baseline_eligible(dict(checks, **{name: False})))
                self.assertFalse(candidate.baseline_eligible({k: v for k, v in checks.items() if k != name}))
        self.assertFalse(candidate.baseline_eligible(dict(checks, unexpected_new_check=False)))

    def test_candidate_workflow_has_no_device_actions_or_automatic_triggers(self):
        w = yaml.load((ROOT.parent / '.github/workflows/camera-device-candidate.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(w['on'], {'workflow_dispatch': {}})
        self.assertEqual(w['permissions'], {'contents': 'read', 'actions': 'read'})
        self.assertEqual(len(w['jobs']), 1)
        job = w['jobs']['camera']
        self.assertEqual(job['env']['CAMERA_SUSFS_REF'], candidate.probe.SUSFS_SHA)
        self.assertNotIn('strategy', job)
        stages = []
        for step in job['steps']:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^actions/(checkout|upload-artifact)@[0-9a-f]{40}$')
            if 'run' in step:
                subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)
                self.assertNotRegex(step['run'], r'\b(adb|fastboot|insmod|rmmod)\b')
                if step['run'].startswith('python3 camera-build/device_candidate.py '):
                    stages.append(step['run'].split()[-1])
            if step.get('with', {}).get('name') == 'experimental-camera-candidate':
                self.assertIn("steps.candidate.outcome == 'success'", step['if'])
        self.assertEqual(stages, ['inputs', 'reference', 'prepare', 'build', 'verify', 'finalize'])

    def test_runner_guard_and_all_stage_dry_runs(self):
        env = dict(os.environ)
        env.pop('GITHUB_ACTIONS', None)
        for stage in candidate.STAGES:
            result = subprocess.check_output([sys.executable, str(ROOT / 'device_candidate.py'), stage, '--dry-run'], env=env, text=True)
            result = json.loads(result)
            self.assertFalse(result['network_or_compilation'])
            self.assertFalse(result['phone_writes'])
            self.assertFalse(result['release_gate_can_open'])
        result = subprocess.run([sys.executable, str(ROOT / 'device_candidate.py'), 'build'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('only on GitHub Actions', result.stderr)


if __name__ == '__main__':
    unittest.main()
