#!/system/bin/sh
# OrangeFox operator command only. --root is for a host rollback rehearsal.
set -eu
ROOT=
if [ "$#" -ne 0 ]; then
    [ "$#" -eq 2 ] && [ "$1" = --root ] || { echo "Usage: rollback-recovery.sh [--root HOST_FIXTURE]" >&2; exit 1; }
    case "$2" in /*) ROOT=${2%/};; *) echo "Fixture root must be absolute" >&2; exit 1;; esac
fi
if [ -z "$ROOT" ]; then
    awk '$2 == "/metadata" {found=1} END {exit !found}' /proc/mounts || { echo "STOP: mount /metadata in OrangeFox first" >&2; exit 1; }
fi
MOD="$ROOT/data/adb/modules/marble-camera-kmd-test"
if [ -L "$MOD" ]; then echo "STOP: unexpected module symlink" >&2; exit 1; fi
if [ -d "$MOD" ]; then touch "$MOD/disable"; fi
for part in metadata metadata/ksu metadata/watchdog metadata/watchdog/ksu; do
    if [ -L "$ROOT/$part" ]; then echo "STOP: unexpected metadata symlink" >&2; exit 1; fi
done
for name in metadata/ksu/modules.rc metadata/watchdog/ksu/modules.rc; do
    rc="$ROOT/$name"
    if [ -L "$rc" ]; then echo "STOP: unexpected RC symlink; inspect manually" >&2; exit 1; fi
    if [ -f "$rc" ] && grep -q '^# marble-camera-kmd-test:one-shot:v1$' "$rc"; then
        digest=$(sha256sum "$rc"); digest=${digest%% *}
        [ "$digest" = "64467cebb57628cad34c11103041bb0092ceacde22e23de15a0cd45706c25031" ] || { echo "STOP: tagged RC contains changes; preserve it and review before reboot" >&2; exit 1; }
        rm "$rc"
        echo "Removed only owned test RC: /$name"
    fi
done
echo "Early test hook removed or absent; unrelated files preserved. Reboot normally to restore stock loading."
echo "No driver was unloaded. Leave the passive test module disabled."
