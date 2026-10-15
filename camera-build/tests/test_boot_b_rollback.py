"""Execute the held recovery procedure on host files, NEVER on block devices.

Only environment-specific block/root/RAM/stat facts are mocked. Hashes, label
files, aliases, geometry, control flow and file content are real fixtures. dd is
a wrapper that refuses anything outside the temporary fixture. This tests the
procedure, not OrangeFox, storage durability, SELinux or actual restoration.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'boot-test/restore-boot-b.sh.HELD'
ORIGINAL = '54ec14c10478432611138c5915876ce524360dd7c87433954abb2d3ec33d4268'


class BootBRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['dev/block/by-name', 'dev/block/bootdevice/by-name',
                     'sys/class/block/sde42', 'sys/class/block/sde54',
                     'tmp/poco-f5-boot-b-rollback', 'bin']:
            (self.root / name).mkdir(parents=True)
        (self.root / 'tmp/recovery.log').write_text('fixture recovery session')
        self.source = self.root / 'tmp/poco-f5-boot-b-rollback/boot_b.img'
        self.source.write_bytes(b'A' * 1536)
        self.source.chmod(0o400)
        self.dest = self.root / 'dev/block/sde42'
        self.dest.write_bytes(b'B' * 1536)
        self.recovery = self.root / 'dev/block/sde54'
        self.recovery.write_bytes(b'R' * 1024)
        for directory in ['dev/block/by-name', 'dev/block/bootdevice/by-name']:
            (self.root / directory / 'boot_b').symlink_to(self.dest)
            (self.root / directory / 'recovery_b').symlink_to(self.recovery)
        for node, label, start, size in [('sde42', 'boot_b', 10, 3), ('sde54', 'recovery_b', 20, 2)]:
            base = self.root / 'sys/class/block' / node
            (base / 'uevent').write_text('PARTNAME=' + label + '\n')
            (base / 'start').write_text(str(start))
            (base / 'size').write_text(str(size))
        self.original = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.current = hashlib.sha256(self.dest.read_bytes()).hexdigest()
        text = SCRIPT.read_text().replace(ORIGINAL, self.original).replace('bytes=201326592', 'bytes=1536')
        for prefix in ['/tmp/', '/dev/', '/sys/']:
            text = text.replace(prefix, str(self.root) + prefix)
        # These fixtures MUST be regular files; device-node test is adapted only here.
        text = text.replace('[ -b ', '[ -f ')
        self.script = self.root / 'held.sh'
        self.script.write_text(text)
        real_stat = shutil.which('stat')
        self.wrapper('id', "print('0')\n")
        self.wrapper('stat', f'''import subprocess,sys
args=sys.argv[1:]
if args[:2]==['-f','-c']: print('tmpfs')
elif '%u:%g:%a' in args: print('0:0:400')
elif '%t:%T' in args: print('103:2c' if args[-1].endswith('sde42') else '103:38')
else: subprocess.run([{real_stat!r}]+args,check=True)
''')
        self.wrapper('blockdev', '''import sys
from pathlib import Path
assert sys.argv[1]=='--getsize64'
print(Path(sys.argv[2]).stat().st_size)
''')
        self.wrapper('dd', '''import os,sys
from pathlib import Path
root=Path(os.environ['FIXTURE_ROOT']).resolve()
args=dict(a.split('=',1) for a in sys.argv[1:])
source,dest=Path(args['if']).resolve(),Path(args['of']).resolve()
assert root in source.parents and root in dest.parents
assert dest.name=='sde42' and args['bs']=='1048576' and args['count']=='192' and args['conv']=='fsync'
(root/'write-called').write_text('only fixture boot_b')
dest.write_bytes(source.read_bytes())
if os.environ.get('CORRUPT_READBACK'): dest.write_bytes(b'corrupt')
''')
        real_sha = shutil.which('sha256sum')
        self.wrapper('sha256sum', f'''import os,sys,subprocess
from pathlib import Path
root=Path(os.environ['FIXTURE_ROOT'])
if sys.argv[-1].endswith('sde42'):
 counter=root/'hash-count'
 count=int(counter.read_text())+1 if counter.exists() else 1
 counter.write_text(str(count))
 if os.environ.get('RACE_DEST') and count==2: Path(sys.argv[-1]).write_bytes(b'C'*1536)
subprocess.run([{real_sha!r}]+sys.argv[1:],check=True)
''')
        self.wrapper('sync', 'pass\n')
        self.env = dict(os.environ, FIXTURE_ROOT=str(self.root),
                        PATH=str(self.root/'bin') + os.pathsep + os.environ['PATH'])

    def wrapper(self, name, text):
        p = self.root / 'bin' / name
        p.write_text('#!' + sys.executable + '\n' + text)
        p.chmod(0o700)

    def run_script(self, mode='--check-only', **env):
        return subprocess.run(['sh', str(self.script), mode, self.current],
                              env=dict(self.env, **env), text=True, capture_output=True)

    def test_production_script_is_held_fixed_backup_and_valid_shell(self):
        self.assertFalse(SCRIPT.stat().st_mode & 0o111)
        self.assertIn('original=' + ORIGINAL, SCRIPT.read_text())
        self.assertIn('bytes=201326592', SCRIPT.read_text())
        self.assertEqual(SCRIPT.read_text().count('\ndd if='), 1)
        subprocess.run(['sh', '-n', str(SCRIPT)], check=True)

    def test_default_preflight_never_writes_and_already_original_is_noop(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ROLLBACK_PREFLIGHT_PASS_NO_WRITE', result.stdout)
        self.assertFalse((self.root/'write-called').exists())
        self.dest.write_bytes(self.source.read_bytes())
        result = self.run_script('--restore-boot-b-with-separate-authorization')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ALREADY_ORIGINAL', result.stdout)
        self.assertFalse((self.root/'write-called').exists())

    def test_write_mode_without_separate_authorization_cannot_write(self):
        result = self.run_script('--restore-boot-b-with-separate-authorization')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root/'write-called').exists())

    def test_correct_fixture_only_restore_and_full_readback(self):
        result = self.run_script('--restore-boot-b-with-separate-authorization',
            BOOT_B_RESTORE_AUTHORIZED='RESTORE_ONLY_BOOT_B_FROM_VERIFIED_BACKUP')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BOOT_B_EXACT_RESTORE_VERIFIED', result.stdout)
        self.assertEqual(self.dest.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.recovery.read_bytes(), b'R' * 1024)

    def test_label_size_hash_alias_overlap_and_race_fail_closed(self):
        # Each subcase has a completely new fixture.
        for case in ['label', 'size', 'source-hash', 'current-hash', 'conflicting-alias',
                     'overlap', 'same-recovery', 'source-symlink', 'prewrite-race']:
            with self.subTest(case=case):
                fixture = BootBRollbackTests()
                fixture.setUp()
                try:
                    if case == 'label': (fixture.root/'sys/class/block/sde42/uevent').write_text('PARTNAME=boot_a\n')
                    elif case == 'size': fixture.dest.write_bytes(b'B')
                    elif case == 'source-hash':
                        fixture.source.chmod(0o600)
                        fixture.source.write_bytes(b'C'*1536)
                        fixture.source.chmod(0o400)
                    elif case == 'current-hash': fixture.dest.write_bytes(b'C'*1536)
                    elif case in ['conflicting-alias', 'same-recovery']:
                        path = fixture.root / ('dev/block/bootdevice/by-name/boot_b' if case=='conflicting-alias' else 'dev/block/by-name/recovery_b')
                        path.unlink()
                        path.symlink_to(fixture.recovery if case=='conflicting-alias' else fixture.dest)
                    elif case == 'overlap': (fixture.root/'sys/class/block/sde54/start').write_text('11')
                    elif case == 'source-symlink':
                        source2 = fixture.root/'tmp/source'
                        fixture.source.rename(source2)
                        fixture.source.symlink_to(source2)
                    result = fixture.run_script('--restore-boot-b-with-separate-authorization',
                        BOOT_B_RESTORE_AUTHORIZED='RESTORE_ONLY_BOOT_B_FROM_VERIFIED_BACKUP',
                        RACE_DEST='1' if case=='prewrite-race' else '')
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertFalse((fixture.root/'write-called').exists())
                    self.assertEqual(fixture.recovery.read_bytes(), b'R'*1024)
                finally:
                    fixture.doCleanups()

    def test_postwrite_corruption_cannot_be_reported_as_success(self):
        result = self.run_script('--restore-boot-b-with-separate-authorization',
            BOOT_B_RESTORE_AUTHORIZED='RESTORE_ONLY_BOOT_B_FROM_VERIFIED_BACKUP', CORRUPT_READBACK='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('BOOT_B_EXACT_RESTORE_VERIFIED', result.stdout)


if __name__ == '__main__':
    unittest.main()
