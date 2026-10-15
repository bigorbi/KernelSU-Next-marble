#!/system/bin/sh
# Operator-invoked, one boot only. It suppresses stock loading; it NEVER loads a driver.
set -eu
ROOT=
MODDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fail() { echo "STOP: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "KernelSU root is required"
[ ! -e "$MODDIR/disable" ] || fail "Test module is disabled"
[ -f "$MODDIR/METADATA-RECOVERY-VERIFIED" ] || fail "Verify OrangeFox metadata access before arming"
[ -f "$MODDIR/payload/ABI-CFI-PASS" ] || fail "Verified candidate is missing"
[ "$(uname -r)" = "$(cat "$MODDIR/payload/kernel.release")" ] || fail "Kernel release mismatch"
awk '$2 == "/metadata" {found=1} END {exit !found}' /proc/mounts || fail "Metadata is not mounted"
for path in /metadata/ksu/modules.rc /metadata/marble-camera-kmd-test; do
    if [ -e "$path" ] || [ -L "$path" ]; then fail "Existing $path; never overwrite it"; fi
done
for path in /metadata /metadata/ksu /metadata/watchdog /metadata/watchdog/ksu; do
    [ ! -L "$path" ] || fail "Unexpected metadata symlink"
done
[ "$(getprop sys.boot_completed)" = 1 ] || fail "Arm only after boot completes"
[ "${1-}" = --authorize-empty-watchdog ] && [ "$#" -eq 1 ] || fail "Explicit operator authorization required: --authorize-empty-watchdog"
. "$MODDIR/metadata-transaction.sh"
rc_empty /metadata/watchdog/ksu/modules.rc || fail "Only the exact reviewed empty watchdog RC is supported"
(cd "$MODDIR/payload" && sha256sum -c SHA256SUMS) || fail "Candidate integrity failed"
stock=/proc/1/root/vendor_dlkm/lib/modules/modules.blocklist
hash=$(sha256sum "$stock"); hash=${hash%% *}
[ "$hash" = "1fd4e3e5e5a13920c2fa75f0cdb7149869ceef6c194f2516ce977c0e492cb706" ] || fail "Stock blocklist changed; review before arming"
hash=$(sha256sum /proc/1/root/vendor/bin/vendor_modprobe.sh); hash=${hash%% *}
[ "$hash" = "389d551584b569fab32321bbb6156ed000c1bab3c94b02f57bc23233039532b8" ] || fail "Installed loader differs from the reviewed ROM"
hash=$(sha256sum /proc/1/root/vendor/etc/init/hw/init.qti.kernel.rc); hash=${hash%% *}
[ "$hash" = "d956b2fbbe77706f1df550639fe6f9c9ef5feffd05add66b432217870e0a20ae" ] || fail "Installed loader differs from the reviewed ROM"
umask 077
mkdir /metadata/marble-camera-kmd-test
cp "$stock" /metadata/marble-camera-kmd-test/modules.blocklist
printf '\nblocklist camera\n' >> /metadata/marble-camera-kmd-test/modules.blocklist
chmod 0644 /metadata/marble-camera-kmd-test/modules.blocklist
chcon u:object_r:vendor_file:s0 /metadata/marble-camera-kmd-test/modules.blocklist || fail "Cannot preserve loader-readable SELinux context"
(cd /metadata/marble-camera-kmd-test && sha256sum modules.blocklist > blocklist.sha256)
for name in metadata-transaction.sh early-guard.sh; do
    cp "$MODDIR/$name" "/metadata/marble-camera-kmd-test/$name"
    chmod 0600 "/metadata/marble-camera-kmd-test/$name"
    chcon u:object_r:metadata_file:s0 "/metadata/marble-camera-kmd-test/$name" || fail "Cannot label early helper"
done
cp "$MODDIR/one-shot-watchdog.rc.example" /metadata/marble-camera-kmd-test/one-shot.rc
chmod 0600 /metadata/marble-camera-kmd-test/one-shot.rc
chcon u:object_r:metadata_file:s0 /metadata/marble-camera-kmd-test/one-shot.rc || fail "Cannot label RC"
rc_capture_and_arm || fail "Arming incomplete; run recovery rollback and inspect preserved files before reboot"
echo "One-boot suppression armed; the exact original empty inode is retained in the private state directory."
echo "Reboot manually. Restore must succeed before the early helper binds the camera blocklist."
echo "No driver was loaded/unloaded. Do not change modules or refresh initrc before the next reboot."
