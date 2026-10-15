import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import boot_format_inventory as boot
import initrc_regression as regression
import kernel_initrc_fix as fix


class InitrcKernelTests(unittest.TestCase):
    def test_actual_pinned_c_regression(self):
        candidates = [ROOT.parent / 'camera-work/kernel/KernelSU-Next',
                      ROOT.parent.parent / 'results/reconstruction/sources/KernelSU-Next']
        manager = next((p for p in candidates if (p / fix.RELATIVE).is_file()), None)
        if manager is None:
            self.skipTest('Pinned KSU source unavailable; workflow sources stage requires this harness')
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'original.c'
            if (manager / '.git').exists():
                source.write_bytes(subprocess.check_output(['git', '-C', str(manager), 'show',
                                                           fix.ci.MANAGER_SHA + ':' + fix.RELATIVE]))
            else:
                source.write_bytes((manager / fix.RELATIVE).read_bytes())
            result = regression.run(source)
        self.assertEqual(result['executions'], 4 * (len(regression.CASES) + 1))
        self.assertTrue(all(case['pass'] for case in result['cases']))

    def test_reject_altered_source_before_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'wrong.c'
            source.write_text('void wrong(void) {}\n')
            with self.assertRaisesRegex(ValueError, 'untouched pinned'):
                regression.run(source)

    def test_crc_audit_rejects_missing_changed_added_exports_and_namespaces(self):
        ref = {'reference_exports': {'a': ['12345678', 'EXPORT_SYMBOL', '', 'vmlinux']},
               'official_kernel_imports': {'a': '12345678'}, 'official_modules': [{}]}
        good = ref['reference_exports']
        self.assertTrue(fix.compare_symbols(good, ref)['official_kernel_imports_match'])
        for actual in [{}, {'a': ['bad', 'EXPORT_SYMBOL', '', 'vmlinux']},
                       {'a': ['12345678', 'EXPORT_SYMBOL', 'changed', 'vmlinux']},
                       {'a': ['12345678', 'EXPORT_SYMBOL', '', 'other-provider']},
                       dict(good, b=['0', 'EXPORT_SYMBOL', '', 'vmlinux'])]:
            with self.subTest(actual=actual):
                self.assertFalse(fix.compare_symbols(actual, ref)['all_baseline_exports_match'])

    def test_reference_is_sanitized_and_provenance_explicit(self):
        ref = fix.reference()
        self.assertTrue(ref['baseline_is_not_running_kernel_binary'])
        self.assertGreater(len(ref['reference_exports']), 16000)
        self.assertGreater(len(ref['official_kernel_imports']), 1000)
        self.assertEqual(ref['existing_conflicts'], [])
        self.assertEqual(fix.ci.digest(ROOT / 'running-config.txt'), ref['reference_config_sha256'])
        self.assertEqual(ref['unchanged_camera_sha256'],
                         'ddf6dd8e764ce8d99f69706c75b5f9aeb61d08542617c93ac0e6a02e170c08ea')
        self.assertNotIn('/home/', json.dumps(ref))

    def test_finalize_retains_hashes_and_failure_but_never_copies_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kout, reports, logs = [root / name for name in ['kernel-out', 'reports', 'logs']]
            for directory in [kout, reports, logs]: directory.mkdir()
            (kout / 'arch/arm64/boot').mkdir(parents=True)
            (kout / 'arch/arm64/boot/Image').write_bytes(b'diagnostic test fixture')
            (kout / 'Module.symvers').write_text('0x12345678 a vmlinux EXPORT_SYMBOL\n')
            (reports / 'kernel-fix-status.json').write_text(json.dumps({
                'status': 'BUILT_NOT_AUDITED', 'kernel_checks_passed': True,
                'boot_publication_allowed': False}))
            (reports / 'kernel-fix-failure.json').write_text(json.dumps({'stage': 'audit'}))
            with patch.multiple(fix.ci, KOUT=kout, REPORTS=reports, LOGS=logs), \
                    patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': ''}):
                fix.finalize()
            result = json.loads((reports / 'kernel-fix-status.json').read_text())
            self.assertEqual(result['status'], 'FAILED_AUDIT')
            self.assertFalse(result['kernel_checks_passed'])
            self.assertFalse(result['boot_publication_allowed'])
            self.assertIn('arch/arm64/boot/Image', json.loads((reports / 'available-build-products.json').read_text()))
            self.assertTrue((reports / 'kernel-Module.symvers').exists())
            self.assertTrue((reports / 'SHA256SUMS').exists())
            self.assertFalse(list(reports.rglob('Image')))

    def test_workflow_is_manual_pinned_and_never_publishes_images(self):
        path = ROOT.parent / '.github/workflows/camera-initrc-kernel.yml'
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['kernel']
        self.assertEqual(len(workflow['jobs']), 1)
        self.assertNotIn('strategy', job)
        self.assertEqual(job['env']['CAMERA_SUSFS_REF'], fix.SUSFS_SHA)
        stages = []
        for step in job['steps']:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^actions/(checkout|upload-artifact)@[a-f0-9]{40}$')
                if step['uses'].startswith('actions/checkout'):
                    self.assertEqual(step['with']['persist-credentials'], 'false')
                    if 'repository' in step['with']:
                        self.assertRegex(step['with']['ref'], r'^[a-f0-9]{40}$')
                else:
                    self.assertIn('always()', step['if'])
                    self.assertIn(step['with']['path'], ['camera-output/logs/', 'camera-output/reports/'])
            if 'run' in step:
                self.assertNotIn('${{', step['run'])
                subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)
                prefix = 'python3 camera-build/kernel_initrc_fix.py '
                if step['run'].startswith(prefix): stages.append(step['run'][len(prefix):])
                self.assertNotRegex(step['run'], r'\b(adb|fastboot|insmod|rmmod|mkbootimg|gh)\b')
        self.assertEqual(stages, list(fix.STAGES))
        for stage in stages:
            result = subprocess.check_output([sys.executable, str(ROOT / 'kernel_initrc_fix.py'),
                                               stage, '--dry-run'], text=True)
            self.assertFalse(json.loads(result)['network_or_compilation'])
            self.assertFalse(json.loads(result)['boot_publication_allowed'])
        env = dict(os.environ)
        env.pop('GITHUB_ACTIONS', None)
        result = subprocess.run([sys.executable, str(ROOT / 'kernel_initrc_fix.py'), 'build'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('only on GitHub Actions', result.stderr)


class BootFormatTests(unittest.TestCase):
    def fixture(self, version=4):
        data = bytearray(16384)
        data[:8] = b'ANDROID!'
        struct.pack_into('<4I', data, 8, 64, 17, 0, 1584 if version == 4 else 1580)
        struct.pack_into('<I', data, 40, version)
        data[4096+56:4096+60] = b'ARM\x64'
        return data

    def inspect(self, data):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'test.img'
            path.write_bytes(data)
            before = path.read_bytes()
            result = boot.inventory(path)
            self.assertEqual(path.read_bytes(), before)
            return result

    def test_both_supported_versions_and_raw_payload_hash(self):
        for version in [3, 4]:
            data = self.fixture(version)
            report = self.inspect(data)
            self.assertEqual(report['header_version'], version)
            self.assertEqual(report['kernel_sha256'], boot.sha(data[4096:4160]))
            self.assertFalse(report['installation_approved'])

    def test_reject_truncated_bad_magic_versions_sizes_and_payloads(self):
        cases = [self.fixture()[:1000], bytearray(4096)]
        for offset, value in [(40, 5), (20, 9000), (8, 0xffffffff), (1580, 0xffffffff), (24, 1)]:
            data = self.fixture(); struct.pack_into('<I', data, offset, value); cases.append(data)
        data = self.fixture(); data[4152:4156] = b'bad!'; cases.append(data)
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.inspect(data)

    def test_avb_footer_extent_is_checked_not_claimed_as_verified(self):
        data = self.fixture()
        struct.pack_into('>4sIIQQQ', data, len(data)-64, b'AVBf', 1, 0, 12288, 12288, 16)
        result = self.inspect(data)
        self.assertEqual(result['avb_footer']['vbmeta_offset'], 12288)
        self.assertFalse(result['cryptographic_avb_verified'])
        struct.pack_into('>Q', data, len(data)-64+20, 12000)
        struct.pack_into('>Q', data, len(data)-64+28, 9999)
        with self.assertRaises(ValueError): self.inspect(data)


if __name__ == '__main__':
    unittest.main()
