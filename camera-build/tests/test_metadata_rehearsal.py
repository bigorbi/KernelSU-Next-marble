"""Test the disposable script with real host filesystem operations and label adapters."""
import os
from pathlib import Path
import subprocess
import unittest
import test_metadata_transaction as fixtures

FILES = fixtures.FILES


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.MetadataTransactionTests(methodName='runTest')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        fixture.wrapper('id', 'print(0)\n')
        fixture.wrapper('awk', 'pass\n')  # Host is not an OrangeFox /metadata mount.
        fixture.wrapper('chcon', '''import os,sys
if os.environ.get('REHEARSAL_BAD_LABEL'): sys.exit(1)
os.setxattr(sys.argv[-1],'user.test.selinux',sys.argv[-2].encode())
''')
        self.before = fixture.attributes(fixture.rc)

    def run_rehearsal(self, **extra):
        # Only adapt the mount root; the production script has no fixture/bypass mode.
        script = (FILES / 'metadata-fs-rehearsal.sh').read_text().replace('BASE=/metadata', 'BASE="$ROOT/metadata"')
        return subprocess.run(['sh', '-c', script], env=dict(self.fixture.env, **extra), capture_output=True, text=True)

    def test_pass_cleans_only_disposable_directory_and_preserves_existing_rc(self):
        result = self.run_rehearsal()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('METADATA_FS_REHEARSAL_PASS', result.stdout)
        self.assertEqual(self.fixture.attributes(self.fixture.rc), self.before)
        self.assertFalse(list((self.fixture.root / 'metadata').glob('marble-camera-fs-rehearsal.*')))

    def test_label_failure_retains_only_inert_rehearsal_files(self):
        result = self.run_rehearsal(REHEARSAL_BAD_LABEL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('METADATA_FS_REHEARSAL_PASS', result.stdout)
        self.assertEqual(self.fixture.attributes(self.fixture.rc), self.before)
        self.assertEqual(len(list((self.fixture.root / 'metadata').glob('marble-camera-fs-rehearsal.*'))), 1)

    def test_missing_exclusive_link_support_stops_without_production_writes(self):
        self.fixture.wrapper('ln', 'import sys\nsys.exit(1)\n')
        result = self.run_rehearsal()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.fixture.attributes(self.fixture.rc), self.before)

    def test_guard_check_is_read_only_and_rehearsal_never_names_production_rc(self):
        script = (FILES / 'metadata-fs-rehearsal.sh').read_text()
        self.assertNotIn('/metadata/watchdog', script)
        self.assertNotIn('rc_capture_and_arm', script)
        for name in ['metadata-fs-rehearsal.sh', 'guard-only-check.sh']:
            subprocess.run(['sh', '-n', str(FILES / name)], check=True)
        guard = (FILES / 'guard-only-check.sh').read_text()
        # Shell syntax audit: commands that could mutate state are absent.
        self.assertNotRegex(guard, r'(?m)^\s*(?:insmod|rmmod|mount|umount|rm|mv|ln|touch|cp|chmod|chown|chcon|setprop|reboot|rc_restore)\b')


if __name__ == '__main__':
    unittest.main()
