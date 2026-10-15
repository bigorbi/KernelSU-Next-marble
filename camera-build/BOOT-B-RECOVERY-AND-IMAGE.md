# POCO F5: corrected Image retention and exact boot_b recovery preparation

Prepared 2026-10-15. **Host preparation only. No workflow dispatch, push, image repack, phone contact, partition write, reboot, slot/AVB change, metadata mutation, guard rearm or camera load was performed.** The raw Image workflow is ready for review; no new Image has been built. Phone-changing commands below are held procedures requiring separate authorization.

## What the completed recovery survey establishes

The actual installed full `boot_b` backup is **201,326,592 bytes**, SHA-256 **`54ec14c10478432611138c5915876ce524360dd7c87433954abb2d3ec33d4268`**. Source-before, laptop copy and source-after agree. The later OrangeFox survey independently reports the same full size/hash for explicit `boot_b`, canonical `/dev/block/sde42`, rdev `103:2c`. Recovery root, reported write access and `/system/bin/dd` were observed; no write was tested.

The captured aliases resolve `recovery` and `recovery_b` to **`/dev/block/sde54`**, and `recovery_a` to `/dev/block/sde27`. Thus the **canonical recovery_b target is distinct from boot_b**. Duplicate aliases agree. The official OTA manifest also contains distinct logical `boot` (192 MiB) and `recovery` (100 MiB) payloads. Together with the user's successful OrangeFox entry and access to `/metadata` and `/data/adb/modules`, this supports a dedicated recovery route. The original OTA recovery is not evidence of the installed OrangeFox bytes.

There are specific limits: the survey did not record recovery_b's rdev, sysfs extent, header/kernel/ramdisk or full hash; it did not establish which image supplied the running recovery kernel. No exact installed OrangeFox device-tree/bootloader source or authenticated boot trace is available. We therefore **do not claim recovery is fully independent of boot_b, vendor_boot, DTBO, vbmeta, slot selection or bootloader policy**, or that recovery entry after a bad kernel has been tested. The earlier recovery of a KernelSU module bootloop demonstrates a different failure case.

AOSP explicitly allows dedicated recovery on A/B devices; the partition layout does not prove a particular OEM boot route. See [AOSP recovery images](https://source.android.com/docs/core/architecture/bootloader/recovery-images). The pinned Linux/KSU sources explain kernel behavior, not Xiaomi's bootloader's selection/verification policy. The historical builder's AnyKernel packaging is not used here: it can include DTB/DTBO and is broader than the requested scope.

Before any future kernel-writing proposal, close the remaining recovery dependency uncertainty with a **targeted read-only recovery capture**: both labels' sysfs PARTNAME, major/minor, parent disk/start/size; a full host backup/hash of installed recovery_b; its boot header and nonzero kernel/ramdisk extents; current recovery kernel banner and the exact operator-observed hardware entry route. A self-contained recovery image plus a documented direct entry route gives stronger evidence, although shared firmware/AVB dependencies remain. Do not deliberately corrupt boot_b to test this.

The held restore procedure additionally refuses an aliased recovery device, wrong labels, different disk topology or overlapping partition extents. Until those facts and the direct entry route are established, this is a prepared, conditional recovery plan, not completed restoration validation.

## Exact-slot rollback contract

Use only this host backup, which remains mode **0400** and unchanged:

```text
~/Desktop/infinity-camera-fix/results/device-test/boot-b-backup/
  capture-20261015T162005Z/boot_b.img
```

Mode 0400 is an operational preservation measure, not a claim of filesystem immutability flags. Keep another independently verified laptop/offline copy before a future device write. The original ROM's boot.img and raw corrected Image are **not** substitutes for this full partition backup.

[boot-test/restore-boot-b.sh.HELD](boot-test/restore-boot-b.sh.HELD) is non-executable and never called by a workflow. Default/check mode performs reads only; its sole write branch requires both an explicit restore argument and a separate literal authorization variable. It enforces:

1. Operator already in the verified OrangeFox session, root and recovery-session evidence present; staged source is the fixed, nonsymlinked RAM path, root:root mode 0400, exact full size and original full hash.
2. Resolve explicit `boot_b` aliases afresh, reject conflicting/dangling/nonblock aliases, require sysfs `PARTNAME=boot_b`, full block size 201,326,592 and matching sysfs sector size. Never select slotless `boot`, boot_a, a UI “Boot” entry or a remembered sde number.
3. Resolve explicit `recovery_b`, require its own PARTNAME and different canonical path/rdev, same expected parent disk and nonoverlapping positive partition extents. Actual active-slot/fallback state must be separately reviewed; the script never switches slots.
4. Read the **entire** current destination hash. If already original, exit with `ALREADY_ORIGINAL` and perform no write. Otherwise require the exact explicitly approved current full-partition hash supplied by the operator. Unknown corruption or an OTA/change in provenance is a stop requiring review, not permission to use an arbitrary hash.
5. Immediately before writing, repeat source full hash and destination identity/full hash. Only then copy 192 × 1 MiB, fsync, sync and read/hash the entire destination again with identity/size checks. Require the exact original SHA-256. No automatic retry, reboot or slot fallback is attempted.

The expected current hash must come from the separately reviewed **full partition-sized test image manifest**, not the raw kernel, a short boot container, or a value captured merely to silence a mismatch. A partial/unknown image requires a new incident assessment. Restoring this exact backup preserves the original header, ramdisk, kernel, padding, stale AVB descriptor and footer. It does not modify vbmeta or roll back shared firmware/counters. Exact-byte rollback is credible only while that surrounding boot-chain context remains unchanged.

## Held future recovery procedure — do not execute now

Prerequisites before staging: separate authorization; same identified POCO F5 already in OrangeFox by the verified physical entry route; no OTA or unrelated boot-chain change; laptop source hash/size verified; sufficient RAM-backed `/tmp` space; required `stat -f -c %T`, `readlink -f`, `blockdev`, `sha256sum`, `chmod`, `chown`, `sync` and `dd conv=fsync` support verified read-only. Unsupported options are a stop, not a reason to bypass a check. Keep recovery powered and USB stable. Do not mount or alter `/metadata` for kernel rollback.

**The following block is a future, separately authorized staging plan.** It copies to recovery RAM, not a partition. It has not been run. Do not paste it during this preparation phase.

```bash
(
  set -euo pipefail
  cd "$HOME/Desktop/infinity-camera-fix"
  capture="$PWD/results/device-test/boot-b-backup/capture-20261015T162005Z"
  test "$(stat -c %s "$capture/boot_b.img")" = 201326592
  ( cd "$capture" && sha256sum -c boot_b.img.sha256 )
  read -r -p 'Verified OrangeFox ADB serial: ' recovery_serial
  test -n "$recovery_serial"
  adb -s "$recovery_serial" shell 'test ! -e /tmp/poco-f5-boot-b-rollback && mkdir -m 700 /tmp/poco-f5-boot-b-rollback'
  adb -s "$recovery_serial" push "$capture/boot_b.img" /tmp/poco-f5-boot-b-rollback/boot_b.img
  adb -s "$recovery_serial" shell 'chown 0:0 /tmp/poco-f5-boot-b-rollback/boot_b.img && chmod 400 /tmp/poco-f5-boot-b-rollback/boot_b.img'
  adb -s "$recovery_serial" push github-camera-fix/camera-build/boot-test/restore-boot-b.sh.HELD /tmp/poco-f5-boot-b-rollback/restore-boot-b.sh.HELD
  adb -s "$recovery_serial" shell 'sha256sum /tmp/poco-f5-boot-b-rollback/boot_b.img /tmp/poco-f5-boot-b-rollback/restore-boot-b.sh.HELD'
)
```

Require the source image's known full SHA and the held script's host SHA from the preparation manifest; compare the phone script hash with the exact host file, not a remembered version. Refuse an existing staging directory instead of overwriting its content. Do not remove it or any other state blindly.

Future check-only command in the already verified recovery shell:

```sh
# Set ONLY from the separately approved full test-partition manifest.
# Leaving this unset intentionally prevents proceeding.
: "${APPROVED_CURRENT_BOOT_B_SHA256:?No approved full-partition hash}"
sh /tmp/poco-f5-boot-b-rollback/restore-boot-b.sh.HELD \
  --check-only "$APPROVED_CURRENT_BOOT_B_SHA256"
```

Require `ROLLBACK_PREFLIGHT_PASS_NO_WRITE` or `ALREADY_ORIGINAL`. Inspect resolved labels/geometry against the captured survey; a changed node number is acceptable only after identity is established through labels/sysfs/size and approved full content. The existence of `/tmp/recovery.log` alone is not proof of OrangeFox or the correct phone; operator identification remains mandatory.

**The next command performs a partition write and remains withheld from execution until separately authorized for a specific incident and expected current hash:**

```sh
: "${APPROVED_CURRENT_BOOT_B_SHA256:?No approved full-partition hash}"
BOOT_B_RESTORE_AUTHORIZED=RESTORE_ONLY_BOOT_B_FROM_VERIFIED_BACKUP \
  sh /tmp/poco-f5-boot-b-rollback/restore-boot-b.sh.HELD \
  --restore-boot-b-with-separate-authorization "$APPROVED_CURRENT_BOOT_B_SHA256"
```

Require `BOOT_B_EXACT_RESTORE_VERIFIED` and the original full digest (or `ALREADY_ORIGINAL` with no write). On I/O failure, disconnect, unexpected state or readback mismatch: **stay in recovery**, preserve output, do not reboot, switch slots or retry blindly. Reboot requires a separate decision after reviewing full readback and boot-chain state. An exact boot_b restore does not repair an independent metadata hook or shared firmware change.

Existing metadata/inode-preserving OrangeFox rollback remains [DEVICE-ROLLBACK.md](DEVICE-ROLLBACK.md). Do not run it as part of this kernel procedure or delete the failed-guard state. Module-folder removal alone does not restore a changed boot kernel. Do not rearm the existing blocked metadata transaction.

## Narrow raw Image workflow

New file: [.github/workflows/camera-initrc-image.yml](../.github/workflows/camera-initrc-image.yml). The previously reviewed diagnostic workflow and all its hashed build inputs remain unchanged. This is **workflow_dispatch only**, contents:read, pinned checkout/upload actions without persisted credentials, one Ubuntu 24.04 job, no matrix, 360-minute limit, two make/ThinLTO jobs and 8 GiB swap. No credentials beyond normal read-only Actions access or special secret is required for these public source/toolchain inputs. Repository Actions must allow the pinned checkout/upload actions; no workflow-write/release/deployment permission is needed.

Repository/branch: `bigorbi/KernelSU-Next-marble`, `camera-fix`. Prepared URL after a separately authorized push: `https://github.com/bigorbi/KernelSU-Next-marble/blob/camera-fix/.github/workflows/camera-initrc-image.yml`. These new files have not been pushed or dispatched. A future manually authorized run must use the reviewed camera-fix commit; do not rerun the original diagnostic workflow expecting it to retain omitted binaries.

The old runner retained only product hash records, not Image/vmlinux bytes. Another compilation is therefore unavoidable to obtain an Image unless a separately preserved binary with the exact reference digest is located. This workflow reuses `kernel_initrc_fix.py`'s exact toolchain/source/config/build/audit functions and verifies the entire previous input manifest before starting. It compiles no external camera/MMRM module, preserves the existing camera candidate and does not invoke the historical AnyKernel packager.

Pins remain kernel `5c59cd9a314704d30f3f66004183426da23410e1`, module headers `83ff8a6f43d2fd897a6ee262f5965d4a9d75f00a`, builder `3577f1d72a7c00c9248882ccac70ac3f15a97baa`, KSU `9a8d3f03db9bd6d51410a6fd732d1db273692ca6`, exploratory SUSFS `ca7093a1a26f7911987110c34a1352d74cfb1d09`. LLVM/LLD 22.1.8 upstream archive SHA `df0e1ecf16caf3489a272a5eea4eec9b0d82878f6477fa309504f918a0006384` is checked. Natural release remains `5.10.269-Zois-17-5c59cd`; all running config bytes and KSU 33321/v3.4.0 metadata must agree. Enforcing CFI, BTI/PAC, ThinLTO, CRCs, KSU/SUSFS and the unchanged boot-kernel CFI helper remain mandatory.

The minimal KSU patch stays `80cbf9e049061489d7a63481ae22cffd32ca30e79ae61bb08b61179e7d229807`, corrected source `6e560f1293606a36e5cd7d4e5ba9fbe4b4c9e21e0dc160a535801d3c3eae5b7b`. All original ten audits rerun, including every export and official import CRC; the source C regression still runs 100 executions. An additional mandatory pinned-Unicorn audit executes nine cases of this new binary's actual fstat arithmetic/cleanup join, and verifies its CFI failure function still panics/traps. Compiler-layout changes that the executor cannot recognize fail closed, not skip. These are static/host checks, not device CFI or init delivery validation.

Hash reference: [evidence/initrc-image-reference.json](evidence/initrc-image-reference.json), authenticated run **38759893534**, commit **764f0f33aeaa21c7715e7255304f43ce3da1151b**. It records all eight original product sizes/hashes and source provenance. Reference Image: **41,376,356 bytes**, SHA **`66361dd2c50eee46ca94379b53fdb1e16a086b42e065da806919ab5254d02647`**. This differs from the installed original kernel SHA `9bf5e2d4bd7e63fec4d046cdebfeeff573179e55b524527c8258bb4fd81e8add`.

For a transparent reproducibility attempt, KBUILD timestamp is explicitly set to the reference build's `Thu Oct 15 15:31:51 UTC 2026` and counter 1. The new GitHub run/commit remains separately recorded; this is not presented as its actual build time. **Hash identity is not promised**: `CONFIG_IKHEADERS=y`, and pinned `kernel/gen_kheaders.sh` used file mtimes when the old timestamp was implicit; that archive's input mtimes were not preserved. Explicit timestamp normalization now affects kheaders too. No kernel bytes, release strings, CRCs or metadata are edited after compilation to obtain equality. Pinned `scripts/mkcompile_h` supports this explicit timestamp mechanism.

After all static gates pass, the new retention helper rehashes all eight real products:

- If every reference size/hash matches, retain `initrc-exact-raw-Image-NOT-BOOT-APPROVAL` with Image, SHA256SUMS, source manifest and review JSON.
- Any product mismatch **fails the workflow's identity gate**. Retain the differing Image only as `initrc-HASH-MISMATCH-quarantine-NOT-REFERENCE-Image` for independent analysis, with actual hashes and all differences. It is never classified as the old validated binary. This avoids discarding evidence again; it does not grant acceptance or weaken ABI/CFI checks.
- An ABI/config/source/CFI/arithmetic failure retains diagnostics but no review Image. Logs and audit artifacts upload even on failure, with 14-day retention. Download and verify promptly.

Neither artifact is an Android boot image or flashable ZIP. No GitHub Release is created. Installation/boot publication gates remain closed regardless of exact hash match. A different binary requires a new explicit binary review before any later packaging proposal. No second run starts automatically.

## AVB and bootloader constraints remain unresolved

The captured installed image is Android boot header **v4**, header 1584, 4096-byte pages, raw ARM64 kernel and legacy-LZ4 newc ramdisk. Boot signature size is zero. This does not mean AVB is disabled: AOSP distinguishes the v4 VTS boot_signature from device-specific verification ([boot header documentation](https://source.android.com/docs/core/architecture/bootloader/boot-image-header)). No repacking or container modification occurs here.

The saved footer geometry is consistent: original image size/vbmeta offset **43,974,656**, vbmeta size **704**. Embedded vbmeta SHA **`1a4fd7c82ef916d31b4365d880bd75ba5557866a3f728a4cd9ddd2dfd714837c`** is identical to the official ROM reference, algorithm NONE, authentication block 0, flags 0, rollback index/location 0. Its logical `boot` hash descriptor still declares **49,090,560 bytes** and overlaps the current vbmeta region. Stored salted digest `ad3c46868566b3e388467486fc086d31cf0dc9b21fda4288f21741279c3747b2` differs from computed `358d73a9df8272d1805193c51766702af94f87a57ad18f31c6f41479f9733f61`. The identical descriptor verifies the original ROM control. Thus **stale embedded metadata is confirmed**; the original full backup preserves it unchanged.

Descriptor name `boot` rather than `boot_b` is normal A/B naming, not the cause of failure. Embedded algorithm NONE/flags 0 do not reveal the active top-level vbmeta policy. Top-level installed vbmeta_b/chain keys/descriptors, OEM bootloader enforcement and trusted lock/rollback state have not been captured. Under standard libavb policy verification errors are fatal in LOCKED state; unlocked allowance is a separate explicit bootloader policy. See [pinned AOSP AVB](https://android.googlesource.com/platform/external/avb/+/761178607206f4cb2af79ed9eec52d8cbd814adb/README.md).

Previously captured Android properties report flash.locked=1, device_state=locked and verifiedbootstate=green. The running config enables `CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y`; root-side values cannot establish hardware boot policy. Neither those strings nor the successful boot of the current modified image proves acceptance of a new one. The exact previous writer and mechanism permitting the stale descriptor remain unknown. Do not unlock/relock, disable verification, edit vbmeta flags/digests/signatures or assume temporary boot support.

Before any modified-kernel proposal, obtain **read-only** installed top-level vbmeta_b/chain evidence and trusted bootloader state/current-slot/slot-health facts by a separately chosen mode/session. Standard bootloader `getvar` information may be useful where supported, but do not enter bootloader mode or change a setting automatically. Unsupported or contradictory facts are a stop, not grounds to assume unlocked. A bootloader that actually enforces the mismatching descriptor with no authorized signing path is a concrete acceptance blocker. The current backup can restore prior bytes only under unchanged trust/rollback/slot context; it cannot guarantee future acceptance.

## Remaining prerequisites and stop conditions

Prepared artifacts can be reviewed now. Cloud compilation/retention, a host-only boot-container proposal and a device test are separate future decisions. No active hook is needed for raw Image retention.

Stop before device writes if recovery's direct entry/dependencies are unresolved; the expected B slot is no longer established; any label/rdev/size/geometry/hash differs; the source or held script hash fails; there is an OTA/boot-chain change; recovery tools/options/RAM are insufficient; or the current destination is not the explicitly reviewed full-image hash. Stop any modified-boot proposal while trusted bootloader/AVB acceptance remains unresolved. Stop Image promotion on any build/security audit or reference hash failure. Do not reinterpret a quarantine artifact as a passed identity gate.

Historical SUSFS source identity remains an explicit experimental uncertainty. Runtime custom-RC delivery, SELinux, PID1 bind timing and camera behavior remain untested under the corrected kernel. Those do not justify bypassing CFI/CRC checks, force-unloading stock camera or automatically rearming the guard. If a future kernel test becomes authorized, its first boot should retain stock camera and inert metadata; the camera guard/load requires a later separate decision.

All **79 host unit tests pass**, including **12 new tests** for the raw Image gate and held rollback procedure. The tests execute the prior emitted fstat arithmetic (nine cases) and independently corrupt an ARM64 add instruction to confirm rejection. Rollback tests operate solely on disposable regular files with block/root/RAM facts and the write operation explicitly mocked; they cover wrong labels, source/destination hashes and sizes, conflicting aliases, overlapping recovery extents, a prewrite content race, missing authorization, already-original no-op and failed readback. They do not test real OrangeFox writes or storage durability. YAML parsing, all embedded shell syntax, Python syntax and local dry-run checks pass; the original reviewed build files remain byte-identical. No actionlint binary was available; validation used the repository's YAML and workflow-structure tests.

Host validation and preservation receipts for this phase are under `results/device-test/boot-b-release-preparation/`, including `host-tests.log`, `STATUS.json`, pre/post preservation receipts and a new file manifest. Reproduce the read-only preservation check from the project root with `python3 results/device-test/boot-b-release-preparation/verify_preparation.py`. The source backup, prior run artifacts, ramdisk/AVB inspection outputs, failed-guard evidence and watchdog inode backups remain unchanged. A passing prepared-procedure test is not a successful phone restore or boot approval.
