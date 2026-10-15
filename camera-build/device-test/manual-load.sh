#!/system/bin/sh
# Operator-invoked only. This file is not service.sh or post-fs-data.sh.
set -eu
MODDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fail() { echo "STOP: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "KernelSU root is required"
[ ! -e "$MODDIR/disable" ] || fail "Test module is disabled"
[ ! -e /sys/module/camera ] || fail "camera is already loaded; reboot, never unload it"
if grep -q '^camera ' /proc/modules; then fail "camera is already loaded"; fi
[ -f "$MODDIR/payload/ABI-CFI-PASS" ] || fail "Verified candidate artifact is missing"
[ "$(uname -r)" = "$(cat "$MODDIR/payload/kernel.release")" ] || fail "Kernel release mismatch"
[ "$(cat /proc/sys/kernel/modules_disabled)" = 0 ] || fail "Kernel module loading is locked"
original=$(sha256sum /proc/1/root/vendor_dlkm/lib/modules/camera.ko); original=${original%% *}
[ "$original" = a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1 ] || fail "Installed original camera file differs"
[ "$(getprop sys.boot_completed)" = 1 ] || fail "Wait until boot completes"
[ "$(getprop vendor.all.modules.ready)" = 1 ] || fail "Vendor module loader is not finished"
grep -q '^blocklist camera$' /proc/1/root/vendor_dlkm/lib/modules/modules.blocklist || fail "Early camera-load suppression is unverified"
ROOT=
. "$MODDIR/metadata-transaction.sh"
rc_paths || fail "Cannot verify retained original RC"
rc_empty "$RC" || fail "Watchdog RC restoration is unverified; do not load"
rc_empty "$BACKUP" || fail "Saved original RC is damaged; rollback first"
[ "$(stat -c '%d:%i' "$BACKUP")" = "$(cat "$STATE/original.identity")" ] || fail "Saved RC identity differs"
[ "$(getprop init.svc.cameraserver)" = stopped ] || fail "Stop cameraserver manually first"
[ "$(getprop init.svc.vendor.camera-provider-2-7)" = stopped ] || fail "Stop vendor.camera-provider-2-7 manually first"
while IFS= read -r name; do
    [ -n "$name" ] || continue
    case "$name" in *[!a-zA-Z0-9_]*) fail "Invalid dependency name";; esac
    [ -d "/sys/module/$name" ] || fail "Dependency $name is absent; do not auto-load a dependency chain"
done < "$MODDIR/payload/required-modules.txt"
(cd "$MODDIR/payload" && sha256sum -c SHA256SUMS) || fail "Candidate integrity check failed"
echo "Loading the verified candidate exactly once. On failure, collect logs and reboot; do not retry or unload."
insmod "$MODDIR/payload/camera.ko" || fail "insmod failed; stop testing and reboot"
[ -d /sys/module/camera ] || fail "camera not present after insmod; stop and reboot"
if [ -f /sys/module/camera/notes/.note.gnu.build-id ] && [ -f "$MODDIR/payload/camera-build-id.note" ]; then
    cmp -s /sys/module/camera/notes/.note.gnu.build-id "$MODDIR/payload/camera-build-id.note" || fail "Loaded build ID differs; stop and reboot"
fi
echo "Candidate loaded. Save dmesg before manually restarting camera services. Rollback is reboot, never rmmod."
