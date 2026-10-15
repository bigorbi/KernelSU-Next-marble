#!/system/bin/sh
# Blocking init exec in the EXISTING KernelSU domain; no new SELinux policy.
set -eu
ROOT=
STATE=/metadata/marble-camera-kmd-test
[ ! -L /metadata ] && [ ! -L "$STATE" ] && [ -d "$STATE" ] || exit 1
[ "$(stat -c '%u:%g:%a' "$STATE")" = 0:0:700 ] || exit 1
[ ! -L "$STATE/metadata-transaction.sh" ] || exit 1
. "$STATE/metadata-transaction.sh"
rc_restore  # Any failure exits BEFORE the bind; the vendor loader stays stock.
# Persist restoration before changing this boot's volatile loader view.
sync
[ ! -L "$STATE/modules.blocklist" ] && [ -f "$STATE/modules.blocklist" ] || exit 1
(cd "$STATE" && sha256sum -c blocklist.sha256) || exit 1
mount -o bind "$STATE/modules.blocklist" /vendor_dlkm/lib/modules/modules.blocklist
