# Minimal KSU/SUSFS init-RC sizing correction

Prepared locally on 2026-10-15. **Not dispatched, kernel-compiled, published, booted or installed.** The first guard failure and its watchdog backups remain intact. The existing camera candidate is unchanged. No second guard is armed.

## Final patch and scope

`ksu-susfs-initrc-size.patch` finalizes the draft without changing its contents. SHA-256:

`80cbf9e049061489d7a63481ae22cffd32ca30e79ae61bb08b61179e7d229807`

It applies to `pershoot/KernelSU-Next@9a8d3f03db9bd6d51410a6fd732d1db273692ca6`, only `kernel/runtime/ksud_integration.c`. The corrected file SHA-256 is `6e560f1293606a36e5cd7d4e5ba9fbe4b4c9e21e0dc160a535801d3c3eae5b7b`. The original file is authenticated by Git blob `630c49cd542d20df2fe0849d8c544eccc8b18584` and SHA-256 `e9e676e00d2de75885525f58b1c433ea1841c2bec41dc413170dea9f5cb99ad1`.

The only functional change is in `CONFIG_KSU_SUSFS`'s `ksu_handle_vfs_fstat()`: after successful `fget()` and the existing `is_init_rc()` filter, call the existing once-only loader, then advertise physical size + built-in RC + the cached custom RC's **actual bytes read**. Add `<linux/limits.h>` and `<linux/overflow.h>` for checked signed arithmetic. The pinned 5.10 source supplies both facilities. A negative size, unrepresentable custom length or overflowing sum leaves the underlying size unchanged, logs the failure and releases the file reference. The callback's void/error convention is unchanged.

For the captured case, **58,427 + 355 + 233 = 59,015**, replacing the insufficient 58,782. The early fstat and subsequent first-read hook use the same cached allocation. Repeated stats obtain the physical stat afresh and add the same snapshot once per result; they do not add onto a previous virtual stat. Read proxies and their stream offsets are unchanged.

Preserved behavior: init/path/regular-file filtering; watchdog priority and fallback only on open failure; disabled-custom-RC switch; empty-file handling; credential override/revert; once-only success **and failure** caching; the existing one-call `kernel_read()` policy; static-entry hook disabling; read/read_iter copying; freeing custom bytes after complete delivery or existing KSU cleanup. Every acquired stat reference and successfully opened custom file is released, including overflow/error paths. A cached snapshot remains owned until consumed or cleaned up; it is not freed prematurely at fstat. The kernel callback may sleep in this existing process-context `vfs_fstat()` path, and KSU credentials are prepared during initialization. No reader, SELinux, mount namespace, timing trigger, syscall signature, SUSFS API, CFI behavior or camera code is patched.

Short custom reads continue to cache only the returned bytes. The correction does not invent unread bytes or add retries to the existing loader. A truncated custom file could still be invalid init syntax; this is an existing behavior, not proof that arbitrary fragments are valid. The already verified 233-byte file is the intended future diagnostic input. This is a scoped correction, not a general concurrency or failed-getattr redesign of the pre-existing hook.

## Regression evidence

All **67 host unit tests pass**. The new harness authenticates the full original C file, applies the final patch only to a temporary copy, and extracts the real loader, cleanup, fstat and both read-proxy functions. Host adapters provide file operations, credentials, allocation and iterator/user copying. Built-in RC text is extracted verbatim (355 bytes), not reimplemented. No substantial kernel/module compilation takes place on the host.

The extracted C runs **100 executions**: 24 corrected scenarios plus the original-bug control, each with `read` and `read_iter`, optimized and AddressSanitizer/UndefinedBehaviorSanitizer builds. Scenarios cover combined and empty RCs, custom-disabled, missing/fallback/nonregular files, allocation failure, zero/error/17-byte short custom reads, repeated stats and reads, negative/overflow/exact-S64-limit sizes, oversized custom length, invalid/unrelated descriptors, cleanup with unconsumed bytes, copy failure/retry in both built-in and custom phases, zero-count reads, underlying read errors, and changing the backing file after the snapshot. Assertions check output byte boundaries/content, EOF, no reload, balanced references/credentials/closes, and no leaked/double-freed allocations at cleanup. Sanitizer instrumentation is for the host harness only; the kernel's CFI/trap flags are not changed.

The unmodified control reproduces 58,782 advertised bytes and **zero** custom bytes delivered. Existing ARM64 reader-loop emulation evidence is preserved rather than rerun. These tests do not prove init action execution or on-device SELinux/mount behavior.

Reproduce:

```bash
cd "$HOME/Desktop/infinity-camera-fix/github-camera-fix"
python3 camera-build/initrc_regression.py \
  --source ../results/reconstruction/sources/KernelSU-Next/kernel/runtime/ksud_integration.c \
  --output /tmp/ksu-initrc-regression.json
python3 -m unittest discover -s camera-build/tests -p 'test_*.py' -v
python3 camera-build/validate.py
python3 camera-build/kernel_initrc_fix.py build --dry-run
```

## Manual cloud build, prepared but not started

New workflow: `.github/workflows/camera-initrc-kernel.yml`. The existing workflows are unchanged. It uses only `workflow_dispatch`, read-only Contents permission, pinned checkout/upload actions, Ubuntu 24.04, one job, `make -j2`, ThinLTO codegen jobs=2, 8 GiB runner swap, a 360-minute timeout and 14-day diagnostic retention. No token/secret, phone connection, manager variant matrix, release publishing, packaging or flashing step is needed.

| Input | Revision / behavior |
| --- | --- |
| Kernel base | `aosp-pablo/android_kernel_xiaomi_sm8450@5c59cd9a314704d30f3f66004183426da23410e1` |
| Module header sources | `aosp-pablo/android_kernel_xiaomi_sm8450-modules@83ff8a6f43d2fd897a6ee262f5965d4a9d75f00a`; camera/MMRM are not rebuilt |
| Historical builder reference | `aosp-pablo/KernelSU-Next-marble@3577f1d72a7c00c9248882ccac70ac3f15a97baa` |
| KernelSU | `pershoot/KernelSU-Next@9a8d3f03db9bd6d51410a6fd732d1db273692ca6`, plus this one patch |
| SUSFS | `ca7093a1a26f7911987110c34a1352d74cfb1d09`, v2.3.0, same recorded exploratory integration |
| SUSFS integration patch SHA-256 | `6e5b676c8e3a30f722d0fef20d131345c8889aa5e9bcf9aaeb29c77759ed621f` |
| Compiler/linker | Upstream LLVM/LLD 22.1.8, commit `ca7933e47d3a3451d81e72ac174dcb5aa28b59d1` |
| LLVM archive SHA-256 | `df0e1ecf16caf3489a272a5eea4eec9b0d82878f6477fa309504f918a0006384` |
| Running configuration SHA-256 | `8a23283724515d0f8a2d91ca472c45824e69b711986f2d6070535d98b6668e50` |
| Natural release required | `5.10.269-Zois-17-5c59cd` |
| KSU compiled metadata | version 33321, tag v3.4.0; historical git count 3321 from the source build logs, not the misleading packaging number 33329 |

LLVM acquisition reuses the existing upstream checksum-verified extractor; it downloads no Android Clang21 toolchain because no camera code is compiled. `ci.py` reconstruction/config/make helpers remain unchanged. The workflow reuses the exact running configuration and refuses **any option difference** after olddefconfig. Kernel ThinLTO is retained; camera's corrected FullLTO flags are not applied to the kernel. The original LLVM22 boot-kernel CFI helper remains unchanged. The separately corrected Clang21 BTI/CFI camera final link and its finished module are untouched.

The build generates authentic `vmlinux`, `Image`, `Module.symvers`, generated headers and release information. Postbuild checks compare the embedded configuration, natural release, actual KSU compile command's ThinLTO/enforcing-CFI flags, trapping runtime symbols/CRCs, KSU compiled metadata and source hashes. It emits CFI/fstat disassembly for review. These are static checks; runtime CFI and actual boot remain unverified.

`evidence/kernel-initrc-reference.json` contains **metadata only**, not proprietary code: all 16,509 exports from the authentic first cloud kernel table, and 3,829 kernel import CRCs collected from 391 official ROM modules including original camera. There are no existing mismatches. The table's authenticated SHA-256 is `1e772632d2d2b37ba92388df4e502aed3146e1410ac9d18b51f51dc17daac03f`. The comparison requires unchanged CRCs/export classes/namespaces/providers and no added/missing exports; tests reject changed, missing, added, namespace-modified and provider-modified entries. The first cloud table is explicitly **not represented as a dump of the running kernel**. Official consumer CRCs provide independent coverage. External-provider binaries and the camera candidate remain unchanged.

Artifacts, including after failure:

- `initrc-kernel-build-logs`: commands, build/config logs, symbol listing, CFI/fstat disassembly and SHA256SUMS.
- `initrc-kernel-review`: source manifests/patches (including copied SUSFS hashes), host regression output, config/release/compiler headers, authentic kernel symbol table, ABI checks, actual build-product hashes, running-versus-build differences and SHA256SUMS.

There is **no boot.img, Image, vmlinux, DTB, module, flashable ZIP or GitHub release upload**. Compiled image/ELF hashes are recorded but those binaries stay on the ephemeral runner. This intentionally avoids publishing a boot image before the remaining boot-container/rollback prerequisites are established. A successful static audit does not open the existing camera release gate or mark anything safe to install. If build inputs change or a check fails, the workflow reports failure rather than rewriting release metadata/CRCs. Workflow unit tests enforce manual triggering and the restricted upload paths.

After separate approval to publish the commit and run this workflow, select branch `camera-fix` in Actions → **Exploratory camera guard kernel correction** → Run workflow. There are no dispatch inputs. This document is not dispatch authorization; no run has been started.

## Boot format: verified reference and missing current backup

The original ROM ZIP was opened read-only and a new `boot.img` extracted to host-only `results/device-test/kernel-initrc-fix/rom-format-reference/`. Its **201,326,592-byte** payload hash matches the existing OTA manifest:

`a4055222afb7e4daff90f73e8f8d013a7fb7e18f005c761a9be4fcb88766ffe7`

The inspected official ROM boot container is **header v4**, header size 1584, fixed 4096-byte pages, uncompressed ARM64 Image payload 46,492,836 bytes, ramdisk 2,591,801 bytes and zero boot-signature bytes. There is a separate AVB footer/vbmeta payload; zero boot-signature size does **not** mean AVB can be ignored. Kernel payload SHA-256 is `17652956b2732342d047f06aed0e1bdebf8e47fe22586714e58ff8e595d34cec`. The existing vendor_boot is also header v4 with 4096-byte pages and OTA hash `4590c0e8d01f8a4a482adbf803b3c4d6c84ee855b53ba96562fea28655cb2041`. Header field definitions follow [AOSP's boot-image format](https://source.android.com/docs/core/architecture/bootloader/boot-image-header); the host reader has malformed/truncated/footer-extent tests. It never repacks/signs an image or claims cryptographic AVB verification.

**This official ROM boot is not the currently running boot.** Its embedded banner reports Clang21, build-user@build-host, Oct 6 08:47:06 CEST; its config SHA is `a1ede035f96aff9b5f339cccf678f23c746bb25aafc8c5e08f607da43c305f36`, with no KSU config entries. The captured running banner is LLVM22/LLD22, marble@github-actions, Oct 6 09:57:44 UTC, and the running config includes KSU/SUSFS. The same release string does not make these kernels identical. Do not use the ROM boot image as an exact KSU rollback/template or change the target to Clang21 because of this discovery.

No verified image of the **installed boot slot**, its exact ramdisk/header/AVB metadata, or an independently verified bootloader acceptance method is present. Captured Android properties show slot `_b`, flash.locked=1, vbmeta.device_state=locked and verifiedbootstate=green. Under root/hiding tools those properties may be spoofed; they establish neither real bootloader locking nor permission to boot unsigned images. Do not disable AVB, alter signing checks or assume `fastboot boot` works. These facts require targeted verification before constructing/publishing a boot image, not another general camera binary audit.

## Recovery and held next steps

OrangeFox access to `/data/adb/modules`, root, ext4 read/write metadata and the required inode/label-preserving tools is accepted as already verified. The disposable metadata rehearsal passed. Existing scripts and recovery kit are unchanged. In the captured failed-guard state the active watchdog RC is empty inode 66305:293, saved original is 66305:286, fallback and retired RC are absent, and there is no blocklist bind. Private one-shot.rc is inert. All **23 archived failure-manifest files still match** their original hashes. Keep that directory and both RC copies intact. Do not clear the transaction directory or rerun the old arming command.

The metadata rollback script does not restore a boot partition. Before any kernel test, separately obtain a host backup of the exact installed boot partition/slot, verify its size and hashes before and after copying, inventory its banner/config/header/ramdisk/AVB, and verify a recovery method that restores **that same partition/slot**. Preserve the original boot, vendor_boot, DTBO, vbmeta and recovery. A verified recovery backup/restore of boot or a verified supported temporary-boot route is required; neither has been demonstrated by the metadata rehearsal. No partition write/slot change or bootloader reboot is authorized here. Merely deleting the KernelSU module folder cannot reverse an incompatible kernel.

Future sequencing, each phone-changing step requiring authorization:

1. Review the local source/test/workflow changes; explicitly authorize any push/dispatch.
2. Inspect cloud compile, complete static audit, source manifest and hashes. Compilation alone is insufficient. Retain exploratory historical-SUSFS uncertainty as documented risk, not invented release provenance.
3. Establish current-boot backup, trusted bootloader state/acceptance, boot-container preservation and exact kernel rollback before enabling any image upload/repack. Do not repack the differing ROM boot just to produce an artifact.
4. If later authorized, first test the corrected kernel with **no guard armed**, stock camera/loading and current empty metadata unchanged; verify release/config/KSU/SUSFS/CFI and normal boot. No experimental camera driver is loaded at that stage.
5. Only after that succeeds, separately plan a fresh guard transaction while preserving this failure state. Expected evidence is 59,015 stat bytes, complete 233-byte custom append, helper restoration and PID1 bind **before** vendor.modprobe. SELinux/namespace/order remain untested until then. Do not reuse the existing blocked transaction or load another driver while stock camera is resident.

Existing metadata recovery commands remain in [DEVICE-ROLLBACK.md](DEVICE-ROLLBACK.md); any phone write is separately authorized. Current state needs no emergency metadata mutation. No rollback script, watchdog backup or candidate was changed in this phase.

## Artifact status and exact-difference limits

| Object | SHA-256 / status |
| --- | --- |
| Final KSU patch | `80cbf9e049061489d7a63481ae22cffd32ca30e79ae61bb08b61179e7d229807` |
| Original official camera.ko, unchanged | `a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1` |
| Existing patched camera.ko, unchanged | `ddf6dd8e764ce8d99f69706c75b5f9aeb61d08542617c93ac0e6a02e170c08ea` |
| New corrected kernel / boot image | **NOT BUILT; no hash exists** |

The intended change from the reconstructed running build is the single SUSFS fstat callback plus two include lines; configuration, base revision, toolchain, KSU interfaces and module remain fixed. A new timestamp/build counter and ThinLTO layout may differ. The historical SUSFS revision is not independently established, so an exact source/binary diff against the running kernel cannot honestly be claimed. The workflow records what it actually used, generated config differences, compiler header, source patch and binary hashes. `evidence/kernel-initrc-inputs.sha256` hashes all prepared input scripts/tests/references; cloud SHA256SUMS will cover actual generated outputs only after an authorized run. No unbuilt artifact is presented as validated.
