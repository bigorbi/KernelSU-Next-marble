#!/system/bin/sh
# Operator-run in OrangeFox. Only a freshly created disposable directory is changed.
# No production RC path is opened, renamed, linked, relabelled or removed.
set -eu
BASE=/metadata
fail() { echo "STOP: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "Recovery root is required"
awk '$2 == "/metadata" && $3 == "ext4" && $4 ~ /(^|,)rw(,|$)/ {ok=1} END {exit !ok}' /proc/mounts || fail "Metadata must be mounted ext4 read/write"
[ ! -L "$BASE" ] || fail "Unexpected metadata symlink"
umask 077
TRIAL="$BASE/marble-camera-fs-rehearsal.$$"
mkdir "$TRIAL" || fail "Disposable directory exists/cannot be created; nothing overwritten"
trap 'echo "Rehearsal failed/interrupted: retain disposable directory $TRIAL for review." >&2' 0
trap 'exit 1' 1 2 15
context() { label=$(ls -Zd "$1"); printf '%s\n' "${label%% *}"; }
empty_hash=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
: > "$TRIAL/original"
chmod 0666 "$TRIAL/original"
chcon u:object_r:metadata_file:s0 "$TRIAL/original"
[ "$(stat -c '%u:%g:%a:%s' "$TRIAL/original")" = 0:0:666:0 ] || fail "Unexpected test file attributes"
[ "$(context "$TRIAL/original")" = u:object_r:metadata_file:s0 ] || fail "Cannot inspect/preserve metadata SELinux context"
before=$(stat -c '%d:%i:%u:%g:%a:%s' "$TRIAL/original")
original_hash=$(sha256sum "$TRIAL/original"); original_hash=${original_hash%% *}
[ "$original_hash" = "$empty_hash" ] || fail "Unexpected dummy content"
printf 'inert camera filesystem rehearsal\n' > "$TRIAL/guard"
guard_hash=$(sha256sum "$TRIAL/guard"); guard_hash=${guard_hash%% *}
sync
mv "$TRIAL/original" "$TRIAL/saved-original"
[ "$(stat -c '%d:%i:%u:%g:%a:%s' "$TRIAL/saved-original")" = "$before" ] || fail "Rename changed inode/attributes"
[ "$(context "$TRIAL/saved-original")" = u:object_r:metadata_file:s0 ] || fail "Rename lost SELinux context"
ln -T "$TRIAL/guard" "$TRIAL/active"
# Failure is required: no -f, no target-directory or symlink traversal.
if ln -T "$TRIAL/saved-original" "$TRIAL/active" 2> "$TRIAL/collision.log"; then fail "Exclusive link replaced an occupied file"; fi
hash=$(sha256sum "$TRIAL/active"); hash=${hash%% *}
[ "$hash" = "$guard_hash" ] || fail "Occupied file changed"
mkdir "$TRIAL/directory-slot"
if ln -T "$TRIAL/saved-original" "$TRIAL/directory-slot" 2>> "$TRIAL/collision.log"; then fail "ln -T accepted a directory destination"; fi
ln -s guard "$TRIAL/symlink-slot"
if ln -T "$TRIAL/saved-original" "$TRIAL/symlink-slot" 2>> "$TRIAL/collision.log"; then fail "ln -T replaced a symlink"; fi
[ -L "$TRIAL/symlink-slot" ] || fail "Symlink collision changed"
mv "$TRIAL/active" "$TRIAL/retired-guard"
ln -T "$TRIAL/saved-original" "$TRIAL/active"
sync
[ "$(stat -c '%d:%i:%u:%g:%a:%s' "$TRIAL/active")" = "$before" ] || fail "Restore changed original inode/attributes"
[ "$(context "$TRIAL/active")" = u:object_r:metadata_file:s0 ] || fail "Restore changed SELinux context"
hash=$(sha256sum "$TRIAL/active"); hash=${hash%% *}
[ "$hash" = "$empty_hash" ] || fail "Restore changed dummy content"
echo "Verified: same inode, root:root, 0666, zero bytes, metadata_file SELinux context."
echo "Verified: rename, exclusive ln -T, occupied-file/directory/symlink refusal, restore."
# Delete only known disposable names. Unexpected additions prevent rmdir, never rm -rf.
rm "$TRIAL/active" "$TRIAL/saved-original" "$TRIAL/guard" "$TRIAL/retired-guard" "$TRIAL/symlink-slot" "$TRIAL/collision.log"
rmdir "$TRIAL/directory-slot"
rmdir "$TRIAL"
trap - 0 1 2 15
echo "METADATA_FS_REHEARSAL_PASS: disposable files removed; production metadata configuration untouched."
