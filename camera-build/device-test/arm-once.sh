#!/system/bin/sh
# Operator-invoked, one boot only. It suppresses stock loading; it NEVER loads a driver.
set -eu
MODDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fail() { echo "STOP: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "KernelSU root is required"
[ ! -e "$MODDIR/disable" ] || fail "Test module is disabled"
[ -f "$MODDIR/METADATA-RECOVERY-VERIFIED" ] || fail "Verify OrangeFox metadata access before arming"
[ -f "$MODDIR/payload/ABI-CFI-PASS" ] || fail "Verified candidate is missing"
[ "$(uname -r)" = "$(cat "$MODDIR/payload/kernel.release")" ] || fail "Kernel release mismatch"
awk '$2 == "/metadata" {found=1} END {exit !found}' /proc/mounts || fail "Metadata is not mounted"
for path in /metadata/ksu/modules.rc /metadata/watchdog/ksu/modules.rc /metadata/marble-camera-kmd-test; do
    if [ -e "$path" ] || [ -L "$path" ]; then fail "Existing $path; never overwrite it"; fi
done
for path in /metadata /metadata/ksu /metadata/watchdog /metadata/watchdog/ksu; do
    [ ! -L "$path" ] || fail "Unexpected metadata symlink"
done
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
mkdir -p /metadata/ksu
# Create and flush a complete file before activating it with an exclusive hard link.
cp "$MODDIR/one-shot-guard.rc.example" /metadata/marble-camera-kmd-test/one-shot.rc
chmod 0600 /metadata/marble-camera-kmd-test/one-shot.rc
sync
ln /metadata/marble-camera-kmd-test/one-shot.rc /metadata/ksu/modules.rc
sync
echo "One-boot stock-load suppression armed. Reboot manually, then verify camera is absent."
echo "The guard attempts to remove its RC pointer before binding the blocklist. OrangeFox rollback handles a retained pointer."
echo "No driver was loaded or unloaded. Do not install/update other modules between arming and the next reboot."
