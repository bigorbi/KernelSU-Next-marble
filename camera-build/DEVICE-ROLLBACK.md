# Reversible camera test rollback

This procedure restores the preflight's exact empty watchdog RC, disables only `marble-camera-kmd-test`, and leaves other configuration intact. No boot/vendor/vendor_dlkm partition, original camera module or userdata is replaced. Read the [transaction and source analysis](WATCHDOG-RC.md) before authorizing any phone writes. Nothing is installed or armed automatically.

## Normal Android

1. Stop camera use. Invoke the reviewed `rollback-recovery.sh` manually through your KernelSU root shell. It creates this passive module's `disable` marker directly; **do not first use Manager's disable/uninstall operation**, which invokes ksud RC regeneration and could discard evidence.
2. The script checks metadata is mounted, validates the saved empty inode/identity/owner/mode/label/content, and restores the original watchdog pathname only if absent or occupied by the exact owned guard. It never overwrites foreign or modified RC contents. An already equivalent empty RC from normal post-fs-data regeneration is preserved. Symlinks or changed backup metadata cause a stop.
3. Require success and inspect the read-only checks below. Then reboot manually. The temporary blocklist bind disappears; the empty watchdog RC injects no action, so the stock early vendor loader loads the original camera. Never rmmod or force-unload the driver. Disabling the userdata folder alone does not neutralize an early metadata hook.

## OrangeFox after a bootloop

1. Enter OrangeFox by the user's already-verified recovery route. Do not wipe/format, flash any partition or change slots. Decrypt/mount `/data` as already verified. Also mount `/metadata` using the recovery fstab and confirm `/proc/mounts` lists it. An unmounted directory is not successful rollback. Do not touch vold or encryption keys.
2. Retain the passive module folder until restoration is complete. Run its recovery script manually in the recovery terminal:

   ```sh
   sh /data/adb/modules/marble-camera-kmd-test/rollback-recovery.sh
   ```

   The supplied companion `metadata-transaction.sh` must be in the same directory. If the folder was already deleted or userdata cannot be decrypted, place the **two reviewed host files** into recovery's `/tmp` and run there:

   ```sh
   adb push camera-build/device-test/rollback-recovery.sh /tmp/rollback-recovery.sh
   adb push camera-build/device-test/metadata-transaction.sh /tmp/metadata-transaction.sh
   adb shell sh /tmp/rollback-recovery.sh
   ```

   These are operator actions requiring authorization, not executed commands. The saved original lives on metadata, so deleting the userdata module does not delete that backup. Do not source an unreviewed helper or delete the metadata directory to fix a failure.
3. Require script success. It restores zero-byte content, root:root 0666 and `u:object_r:metadata_file:s0`, using the saved original inode when restoration is needed. It retains the backup, identity record and any retired guard. It also retains legacy v1 exact-whole-file-hash rollback, checks both RC paths for symlinks/owned hooks, and refuses changed tagged files. A foreign fallback RC remains untouched. The script never reboots or unloads a module.
4. Inspect these read-only outputs before rebooting:

   ```sh
   ls -ldZ /metadata/watchdog/ksu /metadata/watchdog/ksu/modules.rc
   stat -c 'uid=%u gid=%g mode=%a size=%s device=%d inode=%i' /metadata/watchdog/ksu/modules.rc
   sha256sum /metadata/watchdog/ksu/modules.rc
   ls -lZ /metadata/ksu/modules.rc
   ```

   Expect root:root 0666, size 0, SELinux metadata_file, empty SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; the fallback was initially absent. If it has appeared, preserve and review it rather than deleting it. The saved `original.rc` must remain the recorded device/inode and unchanged attributes/content. A daemon-created equivalent active inode can differ and is deliberately preserved.
5. A STOP/nonzero result requires review of current RC, backup and retired files. No forced restoration, shared-file deletion, manual merge or `ksud initrc refresh` is part of recovery. Concurrent writer data is preserved, possibly in the private capture slot; the script reports failure rather than guessing. Keep those files for inspection and do not reboot into a still-owned active guard.
6. Once restoration is verified, leave the module disabled or delete only its userdata folder if desired. Retain `/metadata/marble-camera-kmd-test` for evidence initially; its files are inert once neither active RC contains the guard. Never restore `one-shot.rc` while cleaning up. There is no automatic cleanup command that risks deleting an unexamined backup.
7. Reboot **System** manually. Verify a clean stock early camera load and the original path hash `a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1`. A pathname hash alone cannot identify an already resident driver; the clean reboot matters. Camera behavior should return to its pre-test state without clearing apps or userdata.

## Verified scope

Userdata and basic metadata recovery (ext4 read/write, root, tools, expected RC attributes) are user-verified. Exact commands and the matching host recovery kit are in [OPERATOR-38728953993.md](OPERATOR-38728953993.md). Host tests execute real rename/link restoration, check inode/mode/owner/content/xattrs, cover interruptions and writer collisions, and retain the previous legacy rollback/symlink/foreign-file tests. A host execution of the early shell helper with a mock mount confirms failed restore never reaches a bind. Host owner/SELinux adapters and mocked sync/mount do not establish Android policy, mount namespace or power-loss behavior.

The newly confirmed OrangeFox metadata mount and tool availability are accepted. The prepared disposable rehearsal still checks actual label preservation, same-filesystem rename and exclusive `ln -T` behavior without touching the real RC. Successful recovery inspection does not establish actual early-hook execution. Do not rely on KernelSU volume-key safe mode for this independent early hook. No phone rollback or new camera test has been executed by this project.
