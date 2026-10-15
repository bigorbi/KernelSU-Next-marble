import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'device-test'


class RecoveryDesignTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'data/adb/modules/marble-camera-kmd-test').mkdir(parents=True)
        (root / 'metadata/ksu').mkdir(parents=True)
        return root / 'metadata/ksu/modules.rc'

    def rollback(self, root):
        return subprocess.run(['sh', str(FILES / 'rollback-recovery.sh'), '--root', str(root)], capture_output=True, text=True)

    def test_owned_early_hook_removed_and_repeated_rollback_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); rc = self.fixture(root)
            rc.write_bytes((FILES / 'one-shot-guard.rc.example').read_bytes())
            unrelated = root / 'metadata/ksu/unrelated'; unrelated.write_text('keep')
            self.assertEqual(self.rollback(root).returncode, 0)
            self.assertFalse(rc.exists())
            self.assertTrue((root / 'data/adb/modules/marble-camera-kmd-test/disable').exists())
            self.assertEqual(unrelated.read_text(), 'keep')
            self.assertEqual(self.rollback(root).returncode, 0)

    def test_foreign_rc_and_modified_tagged_rc_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); rc = self.fixture(root)
            foreign = b'on boot\n    setprop unrelated.test 1\n'
            rc.write_bytes(foreign)
            self.assertEqual(self.rollback(root).returncode, 0)
            self.assertEqual(rc.read_bytes(), foreign)
            combined = (FILES / 'one-shot-guard.rc.example').read_bytes() + foreign
            rc.write_bytes(combined)
            self.assertNotEqual(self.rollback(root).returncode, 0)
            self.assertEqual(rc.read_bytes(), combined)

    def test_unexpected_symlink_is_not_followed_or_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); rc = self.fixture(root)
            outside = root / 'foreign'; outside.write_text('keep')
            rc.symlink_to(outside)
            self.assertNotEqual(self.rollback(root).returncode, 0)
            self.assertTrue(rc.is_symlink())
            self.assertEqual(outside.read_text(), 'keep')

    def test_passive_module_has_no_boot_entry_points(self):
        self.assertTrue((FILES / 'skip_mount').is_file())
        for name in ['post-fs-data.sh', 'service.sh', 'boot-completed.sh', 'system', 'system.prop']:
            self.assertFalse((FILES / name).exists())
        for name in ['manual-load.sh', 'rollback-recovery.sh', 'arm-once.sh', 'preflight-readonly.sh']:
            subprocess.run(['sh', '-n', str(FILES / name)], check=True)


if __name__ == '__main__':
    unittest.main()
