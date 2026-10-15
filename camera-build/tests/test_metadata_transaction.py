"""Run the actual shell transaction on real inodes, including writer races.

The host lacks Android SELinux/root ownership. Only stat uid/gid reporting and
ls -Zd are adapted; labels use a user xattr. mv/ln/content/mode/inode behavior is
real. This is not an Android SELinux or recovery validation.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

FILES = Path(__file__).resolve().parents[1] / 'device-test'
LABEL = b'u:object_r:metadata_file:s0'


class MetadataTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rc = self.root / 'metadata/watchdog/ksu/modules.rc'
        self.rc.parent.mkdir(parents=True, mode=0o700)
        self.state = self.root / 'metadata/marble-camera-kmd-test'
        self.state.mkdir(mode=0o700)
        self.rc.write_bytes(b''); self.rc.chmod(0o666)
        os.setxattr(self.rc, 'user.test.selinux', LABEL)
        os.setxattr(self.rc, 'user.extra', b'preserve this too')
        self.initial = self.attributes(self.rc)
        (self.state / 'one-shot.rc').write_bytes((FILES / 'one-shot-watchdog.rc.example').read_bytes())
        self.bin = self.root / 'bin'; self.bin.mkdir()
        real_stat, real_mv, real_ln = [shutil.which(x) for x in ['stat', 'mv', 'ln']]
        self.wrapper('stat', f'''import subprocess,sys
args=sys.argv[1:]
s=subprocess.check_output([{real_stat!r}]+args,text=True).strip()
if args[1] in ('%u:%g:%a','%u:%g:%a:%s'):
 parts=s.split(':'); parts[:2]=['0','0']; s=':'.join(parts)
print(s)
''')
        self.wrapper('ls', '''import os,sys
p=sys.argv[-1]
print(os.getxattr(p,'user.test.selinux').decode()+' '+p)
''')
        self.wrapper('mv', f'''import os,sys
from pathlib import Path
args=sys.argv[1:]
if os.environ.get('RACE_MV') == Path(args[-1]).name:
 p=Path(args[-2]); p.unlink(); p.write_bytes(b'foreign writer data'); p.chmod(0o640)
os.execv({real_mv!r},[{real_mv!r}]+args)
''')
        self.wrapper('ln', f'''import os,sys
from pathlib import Path
args=sys.argv[1:]; p=Path(args[-1])
if os.environ.get('RACE_LN') and p.name == 'modules.rc' and not p.exists():
 if os.environ['RACE_LN']=='directory': p.mkdir()
 else: p.write_bytes(b'concurrent publication')
os.execv({real_ln!r},[{real_ln!r}]+args)
''')
        # Global sync is irrelevant to these host fixture tests; device uses real sync.
        self.wrapper('sync', 'pass\n')
        self.env = dict(os.environ, ROOT=str(self.root), LIB=str(FILES / 'metadata-transaction.sh'),
                        PATH=str(self.bin)+os.pathsep+os.environ['PATH'])

    def wrapper(self, name, code):
        import sys
        p = self.bin / name; p.write_text('#!'+sys.executable+'\n'+code); p.chmod(0o755)

    def attributes(self, p):
        st = p.stat()
        return (st.st_dev, st.st_ino, st.st_uid, st.st_gid, st.st_mode,
                p.read_bytes(), {k: os.getxattr(p, k) for k in os.listxattr(p)})

    def run_transaction(self, function, **extra):
        return subprocess.run(['sh', '-c', 'set -eu; . "$LIB"; '+function],
                              env=dict(self.env, **extra), capture_output=True, text=True)

    def arm(self):
        p = self.run_transaction('rc_capture_and_arm')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)

    def captured(self):
        (self.state / 'original.identity').write_text(f'{self.rc.stat().st_dev}:{self.rc.stat().st_ino}\n')
        self.rc.rename(self.state / 'original.rc')

    def test_complete_arm_restore_preserves_inode_and_all_attributes(self):
        self.arm()
        self.assertEqual(self.rc.read_bytes(), (self.state / 'one-shot.rc').read_bytes())
        result = self.run_transaction('rc_restore')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attributes(self.rc), self.initial)
        self.assertEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertEqual(self.attributes(self.rc), self.initial)

    def test_capture_rejects_nonempty_mode_label_hardlink_and_symlink(self):
        cases = ['content', 'mode', 'label', 'hardlink', 'symlink']
        for case in cases:
            with self.subTest(case=case):
                self.rc.write_bytes(b''); self.rc.chmod(0o666)
                os.setxattr(self.rc, 'user.test.selinux', LABEL)
                if case == 'content': self.rc.write_bytes(b'unrelated configuration')
                if case == 'mode': self.rc.chmod(0o600)
                if case == 'label': os.setxattr(self.rc, 'user.test.selinux', b'foreign_label')
                if case == 'hardlink': os.link(self.rc, self.root / 'extra-link')
                if case == 'symlink':
                    self.rc.unlink(); self.rc.symlink_to(self.root / 'extra-link')
                self.assertNotEqual(self.run_transaction('rc_capture_and_arm').returncode, 0)
                self.assertFalse((self.state / 'original.rc').exists())
                if case == 'hardlink': (self.root / 'extra-link').unlink()

    def test_fallback_or_temporary_writer_collision_rejected(self):
        for p in [self.root / 'metadata/ksu/modules.rc', self.rc.parent / '.modules.rc.tmp']:
            with self.subTest(path=p):
                p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'foreign')
                self.assertNotEqual(self.run_transaction('rc_capture_and_arm').returncode, 0)
                self.assertEqual(p.read_bytes(), b'foreign')
                self.assertEqual(self.attributes(self.rc), self.initial)
                p.unlink()

    def test_crash_after_identity_before_capture_leaves_original(self):
        (self.state / 'original.identity').write_text(f'{self.rc.stat().st_dev}:{self.rc.stat().st_ino}\n')
        self.assertEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertEqual(self.attributes(self.rc), self.initial)

    def test_crash_after_capture_before_publication_restores_original(self):
        self.captured()
        result = self.run_transaction('rc_restore')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attributes(self.rc), self.initial)

    def test_crash_after_retiring_guard_restores_original(self):
        self.arm(); self.rc.rename(self.state / 'retired.rc')
        self.assertEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertEqual(self.attributes(self.rc), self.initial)

    def test_foreign_or_modified_active_rc_never_overwritten(self):
        self.arm(); self.rc.unlink(); self.rc.write_bytes(b'foreign configuration')
        self.assertNotEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertEqual(self.rc.read_bytes(), b'foreign configuration')
        self.rc.write_bytes((FILES / 'one-shot-watchdog.rc.example').read_bytes()+b'# change\n')
        self.assertNotEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertTrue(self.rc.read_bytes().endswith(b'# change\n'))
        self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)

    def test_symlink_or_damaged_saved_original_is_refused(self):
        self.arm(); backup = self.state / 'original.rc'
        backup.chmod(0o600)
        self.assertNotEqual(self.run_transaction('rc_restore').returncode, 0)
        backup.chmod(0o666); backup.unlink(); backup.symlink_to(self.root / 'missing')
        self.assertNotEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertEqual(self.rc.read_bytes(), (self.state / 'one-shot.rc').read_bytes())

    def test_equivalent_ksud_post_fs_refresh_is_preserved(self):
        self.arm(); self.assertEqual(self.run_transaction('rc_restore').returncode, 0)
        self.rc.unlink(); self.rc.write_bytes(b''); self.rc.chmod(0o666)
        os.setxattr(self.rc, 'user.test.selinux', LABEL)
        replacement = self.attributes(self.rc)
        self.assertEqual(self.run_transaction('rc_restore').returncode, 0)
        self.assertEqual(self.attributes(self.rc), replacement)
        self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)

    def test_arm_publication_collision_preserves_both_original_and_writer(self):
        p = self.run_transaction('rc_capture_and_arm', RACE_LN='file')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.rc.read_bytes(), b'concurrent publication')
        self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)

    def test_guard_without_saved_original_is_not_deleted_by_recovery(self):
        (self.state / 'one-shot.rc').unlink(); self.state.rmdir()
        self.rc.write_bytes((FILES / 'one-shot-watchdog.rc.example').read_bytes())
        result = subprocess.run(['sh', str(FILES / 'rollback-recovery.sh'), '--root', str(self.root)],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rc.read_bytes(), (FILES / 'one-shot-watchdog.rc.example').read_bytes())

    def test_failed_guard_ksud_refresh_without_retired_hook_rolls_back_safely(self):
        # Exact failed-boot shape: private original saved, no helper retirement,
        # ksud replaces the active guard with a new equivalent empty inode.
        self.arm(); self.rc.unlink(); self.rc.write_bytes(b''); self.rc.chmod(0o666)
        os.setxattr(self.rc, 'user.test.selinux', LABEL)
        refreshed = self.attributes(self.rc)
        self.assertFalse((self.state / 'retired.rc').exists())
        mod = self.root / 'data/adb/modules/marble-camera-kmd-test'; mod.mkdir(parents=True)
        for _ in range(2):
            result = subprocess.run(['sh', str(FILES / 'rollback-recovery.sh'), '--root', str(self.root)],
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.attributes(self.rc), refreshed)
            self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)
        self.assertTrue((mod / 'disable').is_file())
        self.assertFalse((self.state / 'retired.rc').exists())

    def test_writer_race_during_capture_is_preserved_and_aborts(self):
        p = self.run_transaction('rc_capture_and_arm', RACE_MV='original.rc')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.rc.read_bytes(), b'foreign writer data')
        self.assertEqual((self.state / 'original.rc').read_bytes(), b'foreign writer data')

    def test_writer_race_during_retirement_is_preserved_and_aborts(self):
        self.arm()
        p = self.run_transaction('rc_restore', RACE_MV='retired.rc')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.rc.read_bytes(), b'foreign writer data')
        self.assertEqual((self.state / 'retired.rc').read_bytes(), b'foreign writer data')
        self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)

    def test_exclusive_publication_cannot_overwrite_concurrent_file_or_directory(self):
        self.captured()
        for kind in ['file', 'directory']:
            with self.subTest(kind=kind):
                p = self.run_transaction('rc_restore', RACE_LN=kind)
                self.assertNotEqual(p.returncode, 0)
                if kind == 'file':
                    self.assertEqual(self.rc.read_bytes(), b'concurrent publication'); self.rc.unlink()
                else:
                    self.assertEqual(list(self.rc.iterdir()), []); self.rc.rmdir()
                self.assertEqual(self.attributes(self.state / 'original.rc'), self.initial)

    def test_recovery_entrypoint_restores_and_disables_only_test_module(self):
        self.arm(); mod = self.root / 'data/adb/modules/marble-camera-kmd-test'; mod.mkdir(parents=True)
        result = subprocess.run(['sh', str(FILES / 'rollback-recovery.sh'), '--root', str(self.root)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((mod / 'disable').exists())
        self.assertEqual(self.attributes(self.rc), self.initial)

    def run_early_guard(self):
        # Adapt absolute filesystem roots only. Execute the production shell body;
        # the mount command is recorded rather than performed on this host.
        import hashlib
        (self.state / 'metadata-transaction.sh').write_bytes((FILES / 'metadata-transaction.sh').read_bytes())
        (self.state / 'modules.blocklist').write_bytes(b'blocklist camera\n')
        digest = hashlib.sha256((self.state / 'modules.blocklist').read_bytes()).hexdigest()
        (self.state / 'blocklist.sha256').write_text(digest+'  modules.blocklist\n')
        self.wrapper('mount', "import os\nfrom pathlib import Path\np=Path(os.environ['ROOT'])\nassert (p/'metadata/watchdog/ksu/modules.rc').read_bytes()==b''\n(p/'mount-called').write_text('bind after restore')\n")
        body = (FILES / 'early-guard.sh').read_text().replace('ROOT=\n', 'ROOT="'+str(self.root)+'"\n')
        body = body.replace('STATE=/metadata/marble-camera-kmd-test', 'STATE="'+str(self.state)+'"')
        body = body.replace('[ ! -L /metadata ]', '[ ! -L "'+str(self.root / 'metadata')+'" ]')
        return subprocess.run(['sh', '-c', body], env=self.env, capture_output=True, text=True)

    def test_early_guard_executes_restore_before_mock_mount(self):
        self.arm()
        p = self.run_early_guard()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.root / 'mount-called').exists())
        self.assertEqual(self.attributes(self.rc), self.initial)

    def test_failed_early_restore_never_reaches_mock_mount(self):
        self.arm(); self.rc.unlink(); self.rc.write_bytes(b'foreign rc')
        p = self.run_early_guard()
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.root / 'mount-called').exists())
        self.assertEqual(self.rc.read_bytes(), b'foreign rc')

    def test_new_guard_has_one_blocking_exec_and_no_direct_unchecked_bind(self):
        lines = (FILES / 'one-shot-watchdog.rc.example').read_text().splitlines()
        commands = [s.strip() for s in lines if s.startswith('    ')]
        self.assertEqual(commands, ['exec u:r:ksu:s0 root root -- /system/bin/sh /metadata/marble-camera-kmd-test/early-guard.sh'])
        guard = (FILES / 'early-guard.sh').read_text()
        self.assertLess(guard.index('rc_restore'), guard.index('mount -o bind'))
        for p in FILES.glob('*.sh'): subprocess.run(['sh', '-n', str(p)], check=True)


if __name__ == '__main__':
    unittest.main()
