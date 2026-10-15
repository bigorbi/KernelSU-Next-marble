import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import initrc_image as image


class ImageRetentionTests(unittest.TestCase):
    def test_preflight_frozen_inputs_and_real_dry_runs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.multiple(
                image.ci, REPORTS=Path(temporary)), patch.dict(os.environ, {
                    'KBUILD_BUILD_TIMESTAMP': image.reference()['reproduction_timestamp'],
                    'KBUILD_BUILD_VERSION': '1'}):
            image.preflight()
            result = image.ci.read('image-publication.json')
            self.assertFalse(result['raw_image_identity_verified'])
            self.assertFalse(result['timestamp_is_actual_run_time'])
            self.assertGreater(result['reviewed_input_files_verified'], 60)
            with patch.dict(os.environ, {'KBUILD_BUILD_VERSION': '2'}):
                with self.assertRaisesRegex(ValueError, 'metadata'):
                    image.preflight()
        for stage in image.STAGES:
            output = subprocess.check_output([sys.executable, str(ROOT / 'initrc_image.py'),
                                               stage, '--dry-run'], text=True)
            self.assertFalse(json.loads(output)['network_or_compilation'])
        env = dict(os.environ)
        env.pop('GITHUB_ACTIONS', None)
        result = subprocess.run([sys.executable, str(ROOT / 'initrc_image.py'), 'retain'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Runner-only', result.stderr)

    def test_every_product_hash_size_and_extra_or_missing_output_is_compared(self):
        good = {'Image': {'sha256': 'a', 'size': 8}, 'symvers': {'sha256': 'b', 'size': 1}}
        self.assertEqual(image.compare_products(good, good), {})
        for actual in [{}, dict(good, extra={}), {'Image': good['Image']},
                       dict(good, Image={'sha256': 'different', 'size': 8}),
                       dict(good, Image={'sha256': 'a', 'size': 7})]:
            self.assertTrue(image.compare_products(actual, good))

    @unittest.skipUnless(importlib.util.find_spec('unicorn'), 'Hash-pinned Unicorn executor unavailable')
    def test_actual_prior_machine_code_and_independent_mutation(self):
        text = (ROOT / 'tests/fixtures/initrc-fstat-38759893534.txt').read_text()
        nm = (ROOT / 'tests/fixtures/initrc-fstat-nm-38759893534.txt').read_text()
        self.assertEqual(image.emulate_arithmetic(text, nm)['cases_passed'], 9)
        # Corrupt the immediate add 355 to add 354 in actual ARM64 instruction.
        self.assertIn('b1058d08', text)
        with self.assertRaises(ValueError):
            image.emulate_arithmetic(text.replace('b1058d08', 'b1058908'), nm)
        with self.assertRaises(ValueError):
            image.emulate_arithmetic(text, '')

    def fixture(self, root):
        out = root / 'out'
        reports, logs, kout = [out / 'reports', out / 'logs', root / 'kout']
        for path in [reports, logs, kout / 'arch/arm64/boot']:
            path.mkdir(parents=True)
        ref = copy.deepcopy(image.reference())
        (kout / 'arch/arm64/boot/Image').write_bytes(b'host-only-Image-fixture')
        (kout / 'Module.symvers').write_text('0x12345678 a vmlinux EXPORT_SYMBOL\n')
        products = {name: {'size': (kout/name).stat().st_size,
                          'sha256': image.ci.digest(kout/name)}
                    for name in ['arch/arm64/boot/Image', 'Module.symvers']}
        ref['products'] = products
        (reports / 'kernel-integration.patch').write_text('integration fixture')
        ref['integration_patch_sha256'] = image.ci.digest(reports / 'kernel-integration.patch')
        ref['susfs_copied_inputs'] = {}
        status = {'status': 'STATIC_CHECKS_PASS_BOOT_WITHHELD', 'kernel_checks_passed': True,
                  'checks': {name: True for name in ref['required_checks']}}
        symbol_ref = {'reference_exports': {'a': ['12345678', 'EXPORT_SYMBOL', '', 'vmlinux']},
                      'official_kernel_imports': {'a': '12345678'}, 'official_modules': [{}]}
        comparison = image.fix.compare_symbols(image.fix.symvers(kout / 'Module.symvers'), symbol_ref)
        values = {'kernel-fix-status.json': status, 'sources.json': ref['source_manifest'],
                  'kernel-symbol-comparison.json': comparison, 'susfs-copied-inputs.json': {},
                  'kernel-build-products.json': products, 'image-publication.json': {}}
        for name, value in values.items():
            (reports / name).write_text(json.dumps(value))
        (reports / 'ksud-compile-command.txt').write_text(
            'clang -flto=thin -fsanitize=cfi -fsanitize-cfi-cross-dso '
            '-fno-sanitize-cfi-canonical-jump-tables\n')
        (logs / 'kernel-nm.log').write_text('fixture')
        (logs / 'kernel-cfi-and-size-disassembly.log').write_text(
            '0 <__ubsan_handle_cfi_check_fail_abort>:\n0: 00000000 bl <panic>\n4: 00000000 udf\n')
        return out, reports, logs, kout, ref, symbol_ref

    def test_identity_gate_and_quarantine_never_silently_pass(self):
        for mismatch in [False, True]:
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                out, reports, logs, kout, ref, symbol_ref = self.fixture(root)
                if mismatch:
                    ref = copy.deepcopy(ref)
                    ref['products']['arch/arm64/boot/Image']['sha256'] = '0' * 64
                with patch.multiple(image.ci, OUT=out, REPORTS=reports, LOGS=logs, KOUT=kout), \
                     patch.object(image, 'reference', return_value=ref), \
                     patch.object(image.fix, 'reference', return_value=symbol_ref), \
                     patch.object(image, 'emulate_arithmetic', return_value={'cases_passed': 9}), \
                     patch.dict(os.environ, {'GITHUB_OUTPUT': str(root / 'output')}):
                    if mismatch:
                        with self.assertRaisesRegex(ValueError, 'NOT reproduced'):
                            image.retain()
                    else:
                        image.retain()
                result = json.loads((reports / 'image-publication.json').read_text())
                self.assertEqual(result['raw_image_identity_verified'], not mismatch)
                self.assertFalse(result['installation_approved'])
                self.assertEqual((root/'output').read_text(), 'exact=' + str(not mismatch).lower() + '\n')
                directory = out / ('quarantined-raw-image' if mismatch else 'reviewed-raw-image')
                self.assertEqual((directory/'Image').read_bytes(), (kout/'arch/arm64/boot/Image').read_bytes())
                self.assertEqual(len(list(out.rglob('Image'))), 1)

    def test_security_failure_or_product_tamper_retains_no_image(self):
        for failure in ['cfi', 'missing-check', 'source', 'tamper', 'recorded-failure']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                out, reports, logs, kout, ref, symbol_ref = self.fixture(Path(temporary))
                if failure in ['cfi', 'missing-check']:
                    value = json.loads((reports/'kernel-fix-status.json').read_text())
                    if failure == 'cfi': value['checks']['compiled_callback_retains_thinlto_and_cfi'] = False
                    else: value['checks'].pop(next(iter(value['checks'])))
                    (reports/'kernel-fix-status.json').write_text(json.dumps(value))
                elif failure == 'source':
                    (reports/'sources.json').write_text('{}')
                elif failure == 'tamper':
                    (kout/'arch/arm64/boot/Image').write_bytes(b'changed after audit')
                else:
                    (reports/'kernel-fix-failure.json').write_text('{}')
                with patch.multiple(image.ci, OUT=out, REPORTS=reports, LOGS=logs, KOUT=kout), \
                     patch.object(image, 'reference', return_value=ref), \
                     patch.object(image.fix, 'reference', return_value=symbol_ref), \
                     patch.object(image, 'emulate_arithmetic', return_value={'cases_passed': 9}):
                    with self.assertRaises(ValueError): image.retain()
                self.assertFalse(list(out.rglob('Image')))

    def test_workflow_manual_pinned_bounded_reuses_original_audits(self):
        workflow = yaml.load((ROOT.parent/'.github/workflows/camera-initrc-image.yml').read_text(),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertEqual(len(workflow['jobs']), 1)
        job = workflow['jobs']['kernel']
        self.assertNotIn('strategy', job)
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertEqual(job['env']['KBUILD_BUILD_TIMESTAMP'], image.reference()['reproduction_timestamp'])
        stages = []
        artifacts = {}
        for step in job['steps']:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^actions/(checkout|upload-artifact)@[a-f0-9]{40}$')
                if step['uses'].startswith('actions/checkout'):
                    self.assertEqual(step['with']['persist-credentials'], 'false')
                    if 'repository' in step['with']:
                        self.assertRegex(step['with']['ref'], r'^[a-f0-9]{40}$')
                else:
                    artifacts[step['with']['name']] = step
            if 'run' in step:
                subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)
                self.assertNotIn('${{', step['run'])
                self.assertNotRegex(step['run'], r'\b(adb|fastboot|mkbootimg|insmod|rmmod|gh)\b')
                prefix = 'python3 camera-build/kernel_initrc_fix.py '
                if step['run'].startswith(prefix): stages.append(step['run'][len(prefix):])
        self.assertEqual(stages, [name for name in image.fix.STAGES if name != 'finalize'])
        self.assertTrue(any(step.get('run') == 'python3 camera-build/initrc_image.py finalize' and
                            'always()' in step.get('if', '') for step in job['steps']))
        exact = artifacts['initrc-exact-raw-Image-NOT-BOOT-APPROVAL']
        self.assertIn("steps.image.outputs.exact == 'true'", exact['if'])
        self.assertIn('success()', exact['if'])
        quarantine = artifacts['initrc-HASH-MISMATCH-quarantine-NOT-REFERENCE-Image']
        self.assertIn('failure()', quarantine['if'])
        for name in ['initrc-image-build-logs', 'initrc-image-audit']:
            self.assertIn('always()', artifacts[name]['if'])


if __name__ == '__main__':
    unittest.main()
