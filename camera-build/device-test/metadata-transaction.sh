#!/system/bin/sh
# Library only. ROOT is empty on-device; a nonempty ROOT is used by host fixtures.
# No eval, no shared-file truncation, and no overwrite when publishing a pathname.
rc_fail() { echo "STOP: $*" >&2; return 1; }
rc_paths() {
    RC="$ROOT/metadata/watchdog/ksu/modules.rc"
    STATE="$ROOT/metadata/marble-camera-kmd-test"
    BACKUP="$STATE/original.rc"
    for part in metadata metadata/ksu metadata/watchdog metadata/watchdog/ksu metadata/marble-camera-kmd-test; do
        [ ! -L "$ROOT/$part" ] || { rc_fail "Unexpected metadata symlink: $part"; return 1; }
    done
    [ "$(stat -c '%u:%g:%a' "$ROOT/metadata/watchdog/ksu")" = 0:0:700 ] || { rc_fail "Watchdog RC parent ownership/mode changed"; return 1; }
    [ -d "$STATE" ] && [ "$(stat -c '%u:%g:%a' "$STATE")" = 0:0:700 ] || { rc_fail "Private state directory ownership/mode differs"; return 1; }
}
rc_context() {
    rc_label=$(ls -Zd "$1") || return 1
    printf '%s\n' "${rc_label%% *}"
}
rc_empty() {
    [ ! -L "$1" ] && [ -f "$1" ] || { rc_fail "Expected regular, non-symlink RC: $1"; return 1; }
    [ "$(stat -c '%u:%g:%a:%s' "$1")" = 0:0:666:0 ] || { rc_fail "Empty RC ownership/mode/size changed: $1"; return 1; }
    [ "$(rc_context "$1")" = u:object_r:metadata_file:s0 ] || { rc_fail "Empty RC SELinux context changed: $1"; return 1; }
    rc_hash=$(sha256sum "$1") || return 1
    [ "${rc_hash%% *}" = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 ] || { rc_fail "RC is not the reviewed empty file"; return 1; }
}
rc_owned() {
    [ ! -L "$1" ] && [ -f "$1" ] || return 1
    rc_hash=$(sha256sum "$1") || return 1
    [ "${rc_hash%% *}" = 48b07a40758e91fb16303d1aea20fb7062256a6bac60cbba835d61ddbd104364 ]
}
rc_capture_and_arm() {
    rc_paths || return 1
    for rc_other in "$ROOT/metadata/ksu/modules.rc" "$ROOT/metadata/watchdog/ksu/.modules.rc.tmp" "$BACKUP" "$STATE/original.identity" "$STATE/retired.rc"; do
        if [ -e "$rc_other" ] || [ -L "$rc_other" ]; then rc_fail "Unexpected existing path: $rc_other"; return 1; fi
    done
    rc_empty "$RC" || return 1
    [ "$(stat -c %h "$RC")" = 1 ] || { rc_fail "Original RC has unexpected hard links"; return 1; }
    [ "$(stat -c %d "$RC")" = "$(stat -c %d "$STATE")" ] || { rc_fail "RC backup must be on the same filesystem"; return 1; }
    rc_owned "$STATE/one-shot.rc" || { rc_fail "Guard differs from reviewed v2 template"; return 1; }
    rc_identity=$(stat -c '%d:%i' "$RC") || return 1
    printf '%s\n' "$rc_identity" > "$STATE/original.identity" || return 1
    sync || return 1
    # Atomic same-filesystem rename retains the inode, all xattrs, uid/gid and mode.
    # The destination is private and required absent. No RC content is copied/truncated.
    mv "$RC" "$BACKUP" || return 1
    if ! rc_empty "$BACKUP" || [ "$(stat -c '%d:%i' "$BACKUP")" != "$rc_identity" ]; then
        # A writer raced capture: preserve its inode, publish only if still absent.
        ln -T "$BACKUP" "$RC" || :
        rc_fail "RC changed during capture; saved inode retained; do not reboot until reviewed"; return 1
    fi
    sync || return 1
    # Exclusive link fails if any independent writer has recreated the RC.
    ln -T "$STATE/one-shot.rc" "$RC" || { rc_fail "RC repopulated concurrently; no overwrite performed"; return 1; }
    sync || return 1
}
rc_restore() {
    rc_paths || return 1
    [ ! -L "$STATE/original.identity" ] && [ -f "$STATE/original.identity" ] || { rc_fail "Saved identity is missing or symlinked"; return 1; }
    # A crash after writing the identity but before capture leaves the original intact.
    if [ ! -e "$BACKUP" ] && [ ! -L "$BACKUP" ]; then
        rc_empty "$RC" || return 1
        [ "$(stat -c '%d:%i' "$RC")" = "$(cat "$STATE/original.identity")" ] || { rc_fail "Original was not captured and identity differs"; return 1; }
        echo "Capture never occurred; original empty RC is intact."
        return 0
    fi
    rc_empty "$BACKUP" || return 1
    [ "$(stat -c '%d:%i' "$BACKUP")" = "$(cat "$STATE/original.identity")" ] || { rc_fail "Saved original inode differs"; return 1; }
    [ ! -L "$RC" ] || { rc_fail "Unexpected active RC symlink"; return 1; }
    if [ -e "$RC" ]; then
        # ksud normally regenerates another empty inode at post-fs-data. Leave it
        # untouched if every required attribute/content is already equivalent.
        if rc_empty "$RC" 2>/dev/null; then
            echo "Original attributes/content already restored (or equivalent ksud refresh); preserved."
            return 0
        fi
        rc_owned "$RC" || { rc_fail "Active RC is foreign/modified; saved original retained, no overwrite"; return 1; }
        [ ! -e "$STATE/retired.rc" ] && [ ! -L "$STATE/retired.rc" ] || { rc_fail "Retired slot occupied; inspect before rollback"; return 1; }
        mv "$RC" "$STATE/retired.rc" || return 1
        if ! rc_owned "$STATE/retired.rc"; then
            ln -T "$STATE/retired.rc" "$RC" || :
            rc_fail "Writer raced rollback; captured file preserved; no overwrite"; return 1
        fi
    fi
    # Also handles interruption after capture or after retiring the owned guard.
    ln -T "$BACKUP" "$RC" || { rc_fail "RC repopulated during restore; both files preserved"; return 1; }
    sync || return 1
    rc_empty "$RC" || return 1
    [ "$(stat -c '%d:%i' "$RC")" = "$(cat "$STATE/original.identity")" ] || return 1
    echo "Original empty watchdog RC inode/ownership/mode/SELinux context restored."
}
