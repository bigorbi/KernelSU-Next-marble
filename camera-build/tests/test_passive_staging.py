import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device_candidate import BASELINE_SHA, PATCHED_REQUIRED
from stage_passive_test import stage


class PassiveStagingTests(unittest.TestCase):
    def fixture(self, root, failed_check=None):
        source = root / 'artifact'; source.mkdir()
        # Receipt-handling fixture only; never represented as a compiled module.
        binary = b'not a kernel module: unit-test fixture'
        (source / 'camera.ko').write_bytes(binary)
        checks = {name: name != failed_check for name in PATCHED_REQUIRED}
        (source / 'audit.json').write_text(json.dumps({'binary_safety_checks_pass': True,
            'checks': checks, 'baseline_sha256': BASELINE_SHA,
            'patched_sha256': hashlib.sha256(binary).hexdigest()}))
        (source / 'ABI-CFI-PASS').write_text('fixture')
        (source / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                                               for p in sorted(source.iterdir())))
        return source

    def test_staging_is_passive_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root); dest = root / 'module'
            stage(source, dest)
            self.assertTrue((dest / 'skip_mount').exists())
            self.assertTrue((dest / 'arm-once.sh').exists())
            self.assertFalse((dest / 'service.sh').exists())
            self.assertFalse((dest / 'post-fs-data.sh').exists())
            self.assertFalse(list(root.glob('*.zip')))
            with self.assertRaises(FileExistsError): stage(source, dest)

    def test_failed_crc_or_cfi_receipt_cannot_stage(self):
        for check in ['imports_crc', 'cfi_type_target_groups', 'cfi_published_targets']:
            with self.subTest(check=check), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source = self.fixture(root, failed_check=check)
                with self.assertRaises(ValueError): stage(source, root / 'module')
                self.assertFalse((root / 'module').exists())

    def test_modified_binary_or_unlisted_file_cannot_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root)
            (source / 'camera.ko').write_bytes(b'corrupted')
            with self.assertRaises(ValueError): stage(source, root / 'module')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self.fixture(root)
            (source / 'unlisted').write_text('reject')
            with self.assertRaises(ValueError): stage(source, root / 'module')


if __name__ == '__main__':
    unittest.main()
