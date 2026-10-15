# Controlled marble camera KMD test

**Candidate built and host-staged; not installed or armed by the agent.** The user authorizes experimental device development and accepts temporary bootloop risk. Exact allocated-section and compiled-code equivalence are diagnostics for this experiment. Kernel vermagic, imported/exported CRCs, dependency identity, CFI type/target checks, BTI entry publication and native CFI failure handling remain mandatory. This is separate from release certification; the existing release workflow/policy is unchanged.

## Candidate build completed; guard test awaiting authorization

Current candidate: [run 38728953993](https://github.com/bigorbi/KernelSU-Next-marble/actions/runs/38728953993), SHA-256 `ddf6dd8e764ce8d99f69706c75b5f9aeb61d08542617c93ac0e6a02e170c08ea`. Its 17 required cloud safety checks pass; host metadata/CRCs and 714 CFI publications were independently rechecked. See the exact [operator commands](OPERATOR-38728953993.md); arming and reboot are isolated in a [held guide](HELD-GUARD-38728953993.md) requiring separate authorization.

Reference: [run 38701930741](https://github.com/bigorbi/KernelSU-Next-marble/actions/runs/38701930741), commit `10ce3d0d89d17de0f7c8853a15acda6ad9822172`. Its corrected FullLTO `camera.ko` SHA-256 is `edf7665286ceb2ad1e2d932f2d4fb2f9cc74785185247212075558c020b0ec3b`. It passes all 14 retained original-module checks, including strict original CFI fingerprints and 714 valid published callback fields. Only section/code equivalence fail. Verified artifact ZIP SHA-256: `96ba0a6d7851c4480c3f61f55674fa939b65653cfbe792a52f41cb11387e37dc`, artifact ID 11957367080.

New manual workflow: `.github/workflows/camera-device-candidate.yml`. It authenticates that baseline and the real kernel/MMRM tables from the first build, regenerates headers, applies the verified BTI helper correction, clones a separate module source tree at `83ff8a6f43d2fd897a6ee262f5965d4a9d75f00a`, applies the complete reviewed `camera-fix.patch` (SHA-256 `cbc37f8d259b096cb7625e03e71c4593f4232d5b6d80e84df0449d53f8465d7d`), repeats its existing source-extracted boundary/ownership tests on the runner, and compiles one patched FullLTO module with Android Clang r563880c/build 14054515. No full kernel/MMRM rebuild or substantial local compilation occurs.

Kernel `5c59cd9a314704d30f3f66004183426da23410e1`, KernelSU `9a8d3f03db9bd6d51410a6fd732d1db273692ca6`, exploratory SUSFS `ca7093a1a26f7911987110c34a1352d74cfb1d09`, LLVM 22.1.8 header preparation, natural release `5.10.269-Zois-17-5c59cd`, and all running config values remain pinned. FullLTO retains cross-DSO CFI/noncanonical tables and native trapping under `CONFIG_UBSAN_TRAP=y`; no custom trap-handler flag is added.

Before publishing `experimental-camera-candidate`, the workflow requires:

- Matching ELF architecture, compiler identity, vermagic/modinfo/dependencies, all import/export CRCs, undefined symbols, module structure size and signature format.
- The complete original publication map at valid BTI/B entries; unchanged CFI symbol surface and ARM64 security-property notes.
- Actual Unicorn execution of both baseline and patched dispatchers, comparing every accepted type/destination group and negative wrong-type/alignment checks.
- An unchanged native `__cfi_check_fail` body, no unsupported runtime relocation kinds, and confirmation that the intended KMD helper actually changed in the compiled module.

Counts/layout and unrelated compiled-function differences are recorded experimental risk, not vetoes. The candidate carries its audit, source manifest, patch, hash inventory, dependency list and build-ID note. Any concrete ABI/CFI failure prevents candidate publication; logs/reports still upload. There are no phone commands or installation ZIPs in the workflow. Existing artifact inputs expire October 28/29; verified local copies are retained, and expired cloud inputs must be deliberately replaced rather than silently skipped.

## Actual stock loading: ordinary overlays are too late

These facts come from read-only extraction of the local official ROM, with extracted vendor/vendor_boot images checked against their payload partition hashes:

1. `/vendor/etc/init/hw/init.qti.kernel.rc` starts `vendor.modprobe` on **early-init**. Its `on init` waits for `vendor.all.modules.ready=1`.
2. `/vendor/bin/vendor_modprobe.sh` chooses `/vendor_dlkm/lib/modules` before `/vendor/lib/modules`, reads `modules.blocklist`, filters `modules.load`, and invokes `/vendor/bin/modprobe -b -s -d ... -a ...`. Remaining entries are launched in parallel, then waited for.
3. `camera` is in the load list and absent from the original blocklist. `/vendor/lib/modules` is a symlink to `/vendor_dlkm/lib/modules`. No other modules.dep row depends on `camera.ko`.
4. Both vendor_boot ramdisk fragments were inspected: neither contains `camera.ko`. The fstab first-stage mounts vendor_dlkm and `/metadata`; `/data` is late-mounted and encrypted.
5. The pinned KernelSU's normal module scripts run at **post-fs-data**, later than that loader. A normal file overlay cannot replace a driver already resident in kernel memory. Neither copying a new pathname nor restarting the camera provider replaces it.

The pinned KernelSU has a separate early init.rc hook that reads `/metadata/watchdog/ksu/modules.rc` preferentially, otherwise `/metadata/ksu/modules.rc`, and appends its contents while init parses the primary init.rc. The inspected file matches pinned Git blob `630c49cd542d20df2fe0849d8c544eccc8b18584`. [AOSP init's import parser](https://android.googlesource.com/platform/system/core/+/refs/heads/main/init/import_parser.cpp) defers imports until the containing file finishes, supporting an appended early action preceding imported vendor actions. [Init's documented ordering](https://android.googlesource.com/platform/system/core/+/refs/heads/main/init/README.md) follows parse order. This establishes a plausible early insertion point; the installed device's actual hook, SELinux permissions and ordering still require a guard-only boot. Do not assume successful suppression from static source alone.

## Proposed reversible KernelSU method: manual driver load first

The staged KernelSU folder is passive: `module.prop`, `skip_mount`, manually invoked scripts, a non-executing `.rc.example` template and a checked candidate payload. There is **no** `service.sh`, `post-fs-data.sh`, system overlay or automatic insmod. No installation ZIP is prepared.

`arm-once.sh --authorize-empty-watchdog`, invoked explicitly only after separate authorization for phone writes, supports the exact empty watchdog RC discovered by the user's read-only preflight. It checks root:root, 0666, metadata SELinux label, empty hash/size, one initial link, protected parent, no fallback RC, no writer temporary file and no prior test state. It still requires matching kernel, verified payload, loader/blocklist hashes, mounted metadata and the recovery marker. No arbitrary nonempty configuration is supported.

The [pinned-writer investigation and complete transaction](WATCHDOG-RC.md) explains why `ksud` creates this empty file. The transaction saves its **original inode**, preserving ownership, permissions, content and all xattrs, and exclusively publishes a reviewed guard. A blocking early helper restores the original empty RC **before** binding the camera-only blocklist. Foreign content or a failed restore prevents the bind. Normal daemon regeneration of an equivalent empty file is preserved. Backups/captured files remain available, and no shared RC is truncated or merged. The old absent-file v1 template is retained only for backwards-compatible recovery.

Host replay of the actual loader filter removes only camera, reducing 288 eligible entries to 287 with other entries/order preserved. Dependencies still must be observed loaded before manual insmod; the test never auto-loads a dependency chain. The early exec/domain/mount behavior requires a guard-only device rehearsal. If stock camera loads, `manual-load.sh` refuses to continue.

Static dependency closure of the remaining load list plus first-stage modules reaches all 11 direct camera dependencies, without reaching camera itself. That supports a manual first load after normal boot; it is a prediction, not a substitute for the script's live `/sys/module` checks.

## Operator procedure after a candidate actually builds

Phone-write steps below require separate explicit user authorization. Host staging and document preparation do not authorize copying, arming or loading on the device.

1. The submitted read-only `phone-preflight.txt` is accepted for this investigation; no repeat was requested. If device state changes before the authorized test, an optional read-only recheck can be saved separately:

   ```sh
   adb exec-out "su -c 'sh -s'" < camera-build/device-test/preflight-readonly.sh > ../results/device-test/phone-preflight-recheck.txt
   ```

   Require the expected marble kernel, original camera file hash, module loading not locked, reviewed loader hashes and the exact eligible empty watchdog RC with no fallback configuration. This command is supplied for the operator; it was not run here. This task reused the submitted report without contacting the phone.

2. Stage the downloaded, passing cloud artifact on the **host**:

   ```sh
   python3 camera-build/stage_passive_test.py /path/to/experimental-camera-candidate /new/host/path/marble-camera-kmd-test
   ```

   The helper refuses failed/changed audit receipts, modified payloads, unlisted files or an existing destination. It creates a directory only, never a ZIP or phone installation.

3. After inspecting it, manually copy that passive folder into `/data/adb/modules/marble-camera-kmd-test` using KernelSU root, preserving an existing directory by refusing to overwrite it. Set root ownership, script modes 0755 and ordinary files 0644. Its presence alone does not load anything. KernelSU can display/disable this registered module.

4. The user has now verified OrangeFox root, metadata ext4 read/write, tool availability, exact empty RC attributes and userdata module access. Run the disposable filesystem rehearsal in the operator guide; after PASS the optional passive-copy commands create `METADATA-RECOVERY-VERIFIED`. Do not arm before that operation-level rehearsal. No generic recovery verification needs repeating.

5. In normal Android, explicitly invoke `su -c 'sh /data/adb/modules/marble-camera-kmd-test/arm-once.sh --authorize-empty-watchdog'`, then reboot manually. Do not install/update/enable/disable modules in Manager or run `ksud initrc refresh` between arming and reboot. The first controlled boot must show `/sys/module/camera` absent and no `camera` in `/proc/modules`; vendor loading must be complete, with the extra blocklist visible through `/proc/1/root/vendor_dlkm/lib/modules/modules.blocklist`. Check the watchdog RC has returned to its original zero-byte hash, root:root 0666 and metadata SELinux label. Preserve `/metadata/marble-camera-kmd-test/original.rc` and its identity record. If any condition fails, stop; never unload stock camera.

6. Close camera clients and manually stop `cameraserver` and `vendor.camera-provider-2-7` through root init controls. Then explicitly invoke `manual-load.sh`. It checks the running kernel, stock file identity, boot completion, global suppression, all direct dependencies and payload hashes before one plain `insmod`. It never uses force flags, rmmod, signature/CFI bypasses or retries. A loaded module/build-ID check follows. Any init/probe/loading failure ends this attempt: preserve logs and reboot to stock; do not load another driver in that boot.

7. Save dmesg, then manually restart `vendor.camera-provider-2-7` and `cameraserver`. Test one short rear-camera preview, front camera, switching and video. Save kernel/provider logs and the candidate hash. Stop at the first CFI, memory-lifetime, Oops or recurring camera failure; rollback by reboot rather than unloading. Do not clear app/user data. A successful insmod alone is not a working-camera result.

No automatic loading at boot will be packaged without separate approval. The known historical SUSFS attribution and existing request error-cleanup leak risks remain documented; this patch does not claim to solve all camera lifetime/concurrency defects or change reference ownership. Cold loading with no resident camera avoids unloading active callbacks. Repeated initialization after a failed load and active-driver removal are outside this test method.

## OrangeFox rollback and tested limits

The completed [rollback design](DEVICE-ROLLBACK.md) covers restoration of the saved empty watchdog RC and disabling the passive KernelSU folder. Host fault tests cover interrupted capture/publication/restore, repeated rollback, preserved inode/attributes/xattrs, injected writer races, foreign RC preservation, symlink refusal and no bind after failed restoration. These are actual script tests against simulated recovery filesystems, **not** a claim that the new camera test has been run on the device. Userdata and basic metadata recovery are now user-verified. The exact disposable filesystem rehearsal and one controlled guard boot remain pending; neither was run by the agent.

The existing cloud candidate was downloaded, authenticated and staged on the Linux host. No new compilation/dispatch, push, phone writes, arming, suppressed boot or driver load occurred in this preparation. The exact next commands are in the operator guide; guard arming/reboot and driver loading remain separately authorized stages.
