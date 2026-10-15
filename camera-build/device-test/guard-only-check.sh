#!/system/bin/sh
# Read-only post-boot check. No mount, RC restoration, service control or driver load.
set -eu
MODDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=
fail() { echo "GUARD_ONLY_FAIL: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "Root is required for PID 1 namespace inspection"
[ "$(uname -r)" = "$(cat "$MODDIR/payload/kernel.release")" ] || fail "Kernel release differs"
[ ! -e /sys/module/camera ] || fail "Stock camera is loaded; do not unload or load another driver"
if grep -q '^camera ' /proc/modules; then fail "Camera appears in /proc/modules"; fi
[ "$(getprop sys.boot_completed)" = 1 ] || fail "Boot is incomplete"
[ "$(getprop vendor.all.modules.ready)" = 1 ] || fail "Vendor loading is incomplete"
[ "$(getprop init.svc.vendor.modprobe)" = stopped ] || fail "Vendor modprobe is still running"
. "$MODDIR/metadata-transaction.sh"
rc_paths || fail "State/parent checks failed"
rc_empty "$RC" || fail "Watchdog RC is not restored"
rc_empty "$BACKUP" || fail "Saved original is damaged"
[ ! -L "$STATE/original.identity" ] && [ -f "$STATE/original.identity" ] || fail "Saved identity missing/symlinked"
[ "$(stat -c '%d:%i' "$BACKUP")" = "$(cat "$STATE/original.identity")" ] || fail "Saved inode differs"
rc_owned "$STATE/retired.rc" || fail "Early helper's retired guard is missing/changed"
[ ! -L "$STATE/modules.blocklist" ] || fail "Unexpected blocklist symlink"
(cd "$STATE" && sha256sum -c blocklist.sha256) || fail "Suppression blocklist integrity failed"
expected=$(sha256sum "$STATE/modules.blocklist"); expected=${expected%% *}
actual=$(sha256sum /proc/1/root/vendor_dlkm/lib/modules/modules.blocklist); actual=${actual%% *}
[ "$actual" = "$expected" ] || fail "PID 1 does not see the reviewed suppression blocklist"
awk '$5 == "/vendor_dlkm/lib/modules/modules.blocklist" {ok=1} END {exit !ok}' /proc/1/mountinfo || fail "No blocklist bind in PID 1 namespace"
original=$(sha256sum /proc/1/root/vendor_dlkm/lib/modules/camera.ko); original=${original%% *}
[ "$original" = a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1 ] || fail "Original camera file changed"
[ "$(cat /proc/sys/kernel/modules_disabled)" = 0 ] || fail "Module loading is locked"
(cd "$MODDIR/payload" && sha256sum -c SHA256SUMS) || fail "Candidate payload changed"
count=0
while IFS= read -r name; do
    [ -n "$name" ] || continue
    case "$name" in *[!a-zA-Z0-9_]*) fail "Invalid dependency name";; esac
    [ -d "/sys/module/$name" ] || fail "Dependency $name is absent"
    count=$((count + 1))
done < "$MODDIR/payload/required-modules.txt"
[ "$count" -eq 11 ] || fail "Unexpected dependency count"
echo "GUARD_ONLY_PASS: no camera driver resident; original RC restored; PID 1 bind verified; 11 dependencies present."
echo "STOP HERE. Driver loading and another suppressed boot require separate authorization."
