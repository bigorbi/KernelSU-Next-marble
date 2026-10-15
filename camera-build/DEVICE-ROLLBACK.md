# Reversible camera test rollback

Applies only to module ID `marble-camera-kmd-test` and its exclusively owned one-shot hook. No boot/vendor/vendor_dlkm partition is written. The original ROM ZIP and module backup remain unchanged.

## Normal Android

1. Stop using the camera. Disable the passive test module in KernelSU, or create `/data/adb/modules/marble-camera-kmd-test/disable` through your root shell.
2. Check whether `/metadata/ksu/modules.rc` still contains this test's tagged hook. Normally the early action already removed that pointer. If retained, remove it using the owned-file rollback procedure before reboot. Do not delete an unrelated RC or rely on the normal module-disable marker to neutralize metadata injection.
3. Reboot normally. No camera driver is unloaded manually. The temporary blocklist bind disappears; with the owned RC pointer absent, the original early vendor loader loads the stock module again.

## OrangeFox after a bootloop

1. Enter OrangeFox using the user's already-tested recovery route. Do not wipe or format userdata, flash a ROM/kernel or change slots.
2. Decrypt/mount `/data` as previously verified. Also mount `/metadata`. In the recovery terminal, `mount /metadata` should use the recovery fstab; verify `/proc/mounts` actually lists `/metadata`. If it cannot be mounted, stop rather than treating an empty directory as successful rollback. Do not touch `/metadata/vold` or encryption keys.
3. Open `/data/adb/modules/marble-camera-kmd-test`. Create its `disable` marker. The user may instead delete this exact module folder, as in the previously successful recovery, but **first handle the separate metadata hook**.
4. Run the supplied recovery script manually:

   ```sh
   sh /data/adb/modules/marble-camera-kmd-test/rollback-recovery.sh
   ```

   If the folder was already deleted, copy `camera-build/device-test/rollback-recovery.sh` from this host to recovery's `/tmp` via recovery ADB, then explicitly run it there:

   ```sh
   adb push camera-build/device-test/rollback-recovery.sh /tmp/camera-rollback.sh
   adb shell sh /tmp/camera-rollback.sh
   ```

   These are operator commands, not actions executed by this project. The script checks metadata is mounted, disables only this test module if accessible, and removes an RC only if its entire contents match the known owned template. It checks both `/metadata/ksu/modules.rc` and the higher-priority `/metadata/watchdog/ksu/modules.rc`, preserves foreign content, refuses unexpected symlinks or modified tagged files, and does not reboot automatically.
5. If an RC is reported changed, preserve it for inspection and do not blindly delete shared configuration. The arming method requires both RC paths initially absent. For the untouched test template, removal restores that exact prior state. If another tool modified a shared RC, review/restore its saved state instead; this design intentionally refuses to guess.
6. Files under `/metadata/marble-camera-kmd-test/` are inert after the active RC pointer is gone. They may be retained for evidence or removed after confirming their exclusive ownership. Do not restore the test RC when cleaning up. No original image/module needs restoration because none was overwritten.
7. Reboot **System** normally. Confirm the original `camera.ko` path still hashes to `a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1`, the original camera loads during early boot, and camera behavior returns to the pre-test state. A pathname hash alone cannot identify an already resident driver; the clean reboot and early-load evidence matter.

## What has actually been tested

- The user verified OrangeFox access to `/data/adb/modules` and successful boot recovery after deleting an unrelated KernelSU module.
- Host execution of the real rollback script removes only the exact owned hook, marks this module disabled, preserves other files, succeeds when repeated, preserves foreign/modified RC content, and refuses symlinks.
- The real vendor loader's filter was replayed without modprobe: only the `camera` entry is suppressed, with 287 other eligible entries unchanged.
- The early hook, bind permission, OrangeFox metadata access and candidate load are **not yet device-tested**. Do not describe this design as device-validated until the operator completes that rehearsal. Do not rely on KernelSU volume-key safe mode for this independent early metadata hook.
