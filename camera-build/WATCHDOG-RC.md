# Empty watchdog RC: controlled one-boot transaction

Status: host implementation and fault tests only. No phone writes, arming, push, workflow dispatch or module compilation was performed for this change. The read-only preflight is retained privately under `results/device-test/phone-preflight.txt`.

## Confirmed pinned implementation

Inspected `pershoot/KernelSU-Next@9a8d3f03db9bd6d51410a6fd732d1db273692ca6`; downloaded userspace files were verified against the previously retained Git tree's blob IDs. The public evidence inventory is [watchdog-rc-source.json](evidence/watchdog-rc-source.json).

- [`module.rs`, lines 475–594](https://github.com/pershoot/KernelSU-Next/blob/9a8d3f03db9bd6d51410a6fd732d1db273692ca6/userspace/ksud/src/module.rs#L475): `ksud`'s `regenerate_preinit_rc()` chooses watchdog when its directory exists, creates a temporary file, collects custom RCs, syncs and renames it into `modules.rc`, even when nothing was collected. It labels the result `metadata_file` and removes the alternative stale RC.
- [`init_event.rs`, lines 10–73](https://github.com/pershoot/KernelSU-Next/blob/9a8d3f03db9bd6d51410a6fd732d1db273692ca6/userspace/ksud/src/init_event.rs#L10): post-fs-data sets umask zero and refreshes the file. Default creation mode therefore explains 0666. Module mutation operations and `ksud initrc refresh` also regenerate it.
- [`ksud_integration.c`, lines 248–326](https://github.com/pershoot/KernelSU-Next/blob/9a8d3f03db9bd6d51410a6fd732d1db273692ca6/kernel/runtime/ksud_integration.c#L248): the kernel chooses an opened watchdog file first. Size zero skips injection; it does **not** cause fallback to the other path. The contents are cached once for primary init.rc parsing.

The observed root:root, 0666, `u:object_r:metadata_file:s0`, zero-byte file is consistent with ordinary `ksud` output. It is not evidence of an unrelated watchdog-service configuration. Source tracing identifies the intended writer; the preflight cannot prove the historical writer process or the exact installed userspace daemon revision. No creator audit trail is available.

## Narrow eligibility

Only `/metadata/watchdog/ksu/modules.rc` with ALL these properties is supported: regular non-symlink file, uid/gid 0:0, mode 0666, size zero, SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, SELinux context `u:object_r:metadata_file:s0`, and initially one hard link. Its parent must remain root:root 0700. Ancestor symlinks, another RC at `/metadata/ksu/modules.rc`, a writer's `.modules.rc.tmp`, or an existing test state directory prevent arming. Parent attributes are never changed. Nonempty RC merging is unsupported.

The operator must explicitly authorize phone writes separately and invoke `arm-once.sh --authorize-empty-watchdog`. The script still requires the candidate safety receipt, kernel/loader/payload hashes and metadata recovery marker. These are necessary conditions, not automatic installation approval. Run only one test-management command at a time; do not install, remove, enable/disable modules in Manager, run initrc refresh, or update root tools between arming and reboot. No cooperating lock with ksud exists; the transaction detects/preserves concurrent files rather than claiming mutual exclusion.

## Preservation and one-boot behavior

1. Prepare a private root-owned 0700 `/metadata/marble-camera-kmd-test` with complete helpers, RC and a blocklist copy containing only the added camera entry. Flush it before activation. The temporary guard is root-owned 0600 with the kernel-readable metadata label.
2. Record the original device/inode and atomically rename the existing empty RC to `original.rc` in that private directory on the **same filesystem**. Its inode retains uid/gid, mode, content and every xattr, including SELinux. No copy/chmod/chown/chcon is applied to the original. Recheck the captured inode and eligibility; retain and attempt exclusive return if a writer raced capture.
3. Publish the complete guard with `ln -T` into the absent shared pathname. No force flag is used: a concurrent file, directory or symlink prevents publication without overwriting it. The reviewed template's entire SHA-256 must match. The original is synced before guard publication.
4. On early-init a **blocking** init exec runs `early-guard.sh` from metadata using the existing `u:r:ksu:s0` root domain. The pinned rules already grant init access to that domain; no SELinux policy is added and enforcing mode is unchanged. This avoids using late userdata files. The old direct `rm; mount` hook is not used for this file.
5. The helper validates saved original content, attributes and recorded identity. It retires only the exact owned RC, checking again after capture. It publishes the saved original using exclusive `ln -T`, syncs, then verifies the blocklist and attempts its bind mount. Any restoration/verification/exec failure occurs before the bind, leaving the ordinary loader view. A bind failure also leaves stock loading. The kernel already cached this boot's RC, so restoring the empty pathname does not cancel that cached action.
6. Normal post-fs-data `ksud` may regenerate an equivalent empty inode after the early restoration. Rollback preserves such an existing file if its exact content, owner, mode and label already match. It does not overwrite it merely to recover the earlier inode. The original backup remains intact for evidence. A new nonempty or differently attributed RC stops rollback for review.

The original and any retired/captured file are retained. New links/renames can change link count and ctime; this is not a forensic timestamp snapshot. At restoration before any normal daemon refresh, the active path points to the original inode. No original bytes, ownership, mode or xattrs are reconstructed from a lossy copy.

## Interruption and collision handling

| Interruption or change | Result / recovery |
| --- | --- |
| Preparation only, no identity/capture | Original path untouched; staging remains inert. |
| Identity synced, capture not done | Rollback verifies the original inode is still at the path. |
| Original captured, guard not linked | Rollback exclusively links the saved original back. Next boot without a guard otherwise uses stock loading. |
| Guard linked; early hook not run or boot interrupted | OrangeFox rollback validates and retires the exact guard, restores the saved original. |
| Guard retired, restore interrupted | Backup remains; repeated rollback restores into the absent path. |
| Writer changes RC before capture/retirement | Captured bytes/inode are retained and exclusively returned if possible; operation fails. It never truncates or discards them. |
| Writer creates a new path during publication | Exclusive link fails; current file and saved original are both preserved. Review before reboot. |
| Foreign/modified RC, bad backup attributes/identity, unexpected symlink | Stop with evidence intact; do not force restoration or delete configuration. |
| Normal equivalent empty daemon refresh | Leave the current equivalent file untouched; retain original backup. |

`ln -T`, `stat -c`, `ls -Zd`, xattr-preserving same-filesystem rename and SELinux label inspection must work in Android and OrangeFox. Unsupported commands fail closed. Android init exec/domain transitions and mount-namespace/SELinux behavior are **not host-proven**. If suppression fails, the existing manual-load check rejects the already loaded stock camera; no unload or force path exists.

## Tests and remaining device prerequisites

**Validation: 53 host tests passed, including 18 metadata-transaction/early-helper tests.** Python compilation, shell syntax checks and `git diff --check` also pass. No large compilation or binary-equivalence analysis was run.

The transaction tests execute real shell functions and filesystem rename/link operations. They check uid/gid/mode/content, original inode and all available xattrs before/after restore, interrupted operations, repeat rollback, foreign and modified content, symlinks, bad permissions/labels, hard-link collisions, and injected writer races. Tests execute the early helper with mount replaced by a recorder and prove failed restoration never reaches it. Host uid/gid reporting and SELinux label inspection are adapters; labels are represented by a user xattr. Global sync is stubbed in fixtures. These do **not** validate Android SELinux enforcement or power-loss durability.

The previous legacy v1 whole-file-hash rollback, symlink refusal, mount check, unrelated-file preservation, module disable and ABI/CFI candidate gates remain. Recovery must have the reviewed `rollback-recovery.sh` **and** companion `metadata-transaction.sh` if the passive module folder was deleted.

Userdata module recovery is user-verified. The submitted Android preflight does not establish OrangeFox `/metadata` mount/label-read/rename/link access. Retain that separate recovery prerequisite and perform the first guard-only boot only after explicit authorization. A passing patched camera artifact remains required; nothing here builds, installs or auto-loads one. See [installation](DEVICE-TEST.md) and [rollback](DEVICE-ROLLBACK.md).
