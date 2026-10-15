# First guard boot: root cause and safe stop state

**Do not rearm this metadata-RC method on the current kernel.** The first user-authorized guard boot finished with stock camera loaded. No second boot, driver loading/unloading, phone installation, metadata change, policy change or cloud build was performed during this investigation. The camera candidate remains untested on-device.

## Confirmed cause: custom RC bytes never reached init

The user-captured `results/device-test/guard-failed-dmesg.txt` records:

| Boot time | Evidence |
| --- | --- |
| 0.872178 | KernelSU reports primary init.rc size **58,427 → 58,782**. The addition is exactly **355**. |
| 0.873308 | KernelSU loads **233 bytes** from the watchdog RC. |
| 0.873309 | The read hook knows both lengths: built-in **355**, custom **233**. |
| 0.873528 | It appends built-in **355** and completes that portion. |
| 0.873529 | `read_iter_proxy: module append error, appended 0`. |
| 1.139236 | The vendor module loader executes. |
| 1.902984 | Spectra camera reports successful initialization. |
| 6.729664 | Normal KernelSU post-fs-data is triggered. |

The [pinned SUSFS fstat callback](https://github.com/pershoot/KernelSU-Next/blob/9a8d3f03db9bd6d51410a6fd732d1db273692ca6/kernel/runtime/ksud_integration.c#L539) adds only `ksu_rc_len`. It neither snapshots the custom RC first nor adds `module_rc_len`. The non-SUSFS callback in the same file already handles the custom length. The authenticated integration patch calls this callback from `vfs_fstat()` under CONFIG_KSU_SUSFS, matching the running configuration and log messages.

The actual phone's `/system/lib64/libbase.so` was read to the host, not modified. SHA-256: `d32279af8f0b6944dc9622ee3f6b4831da2b6bdb490e6e5450cf2ee7a8b5d455`. Its exported `ReadFdToString` at 0x100cc reads positive-size regular files using the fstat size as a bound. The loop at 0x102a8–0x1030c requests only remaining bytes and exits when that bound is filled. It does not make an extra read after receiving the advertised 58,782 bytes. Init's captured ELF links libbase. The loaded-library mapping was not separately captured; the decisive kernel no-delivery evidence does not depend on that additional attribution. This is actual binary evidence; the generic AOSP main-branch implementation with a 4096-byte loop is not assumed to match this ROM.

Thus the final read has room for only the built-in 355 bytes. `copy_to_iter` then returns zero for the custom fragment because no iterator capacity remains. This is not evidence of an SELinux denial or a failed blocklist mount. The required total would be **58,427 + 355 + 233 = 59,015** bytes.

The `ksu_init_rc_hook is disabled` log is the pinned hook's normal one-shot installation path: it disables the static entry hook before proxying that first init read. It is not evidence that the user disabled custom RCs; the subsequent custom-file load proves otherwise.

## Independent offline reproduction

`reproduce_initrc_budget.py` executes the captured ARM64 reader loop under existing Unicorn 2.1.4. File reads and KSU's injected stream are explicitly modeled; it does not execute init actions, a kernel or any phone command.

| Case | Advertised total | Custom bytes delivered |
| --- | ---: | ---: |
| Shipped callback, 233-byte guard | 58,782 | **0** |
| Correct total, same guard | 59,015 | **233** |
| Shipped callback, one-byte fragment | 58,782 | **0** |
| Empty custom fragment | 58,782 | 0, built-in RC unchanged |

All four cases pass. The shipped case requests 58,782 bytes, receives 58,427 physical bytes, then requests/receives 355 and stops. Correct sizing produces a second request of 588, delivering 355 built-in plus 233 custom bytes. Shortening/reordering the shell fragment cannot fix the missing byte budget.

Reproduce on the host with the already installed verifier environment:

```bash
cd "$HOME/Desktop/infinity-camera-fix/github-camera-fix"
../results/actions-run-38603388362/investigation/venv/bin/python \
  camera-build/reproduce_initrc_budget.py \
  ../results/device-test/guard-failure-1/reader-binaries/libbase.so \
  camera-build/device-test/one-shot-watchdog.rc.example \
  --output /tmp/marble-initrc-budget.json
```

Results are saved locally in `results/device-test/guard-failure-1/reader-budget-reproduction.json`. This proves delivery behavior for the captured loop and modeled kernel stream, not successful parsing, SELinux execution or mounting after a future kernel correction.

## Init parsing, early-init ordering and namespaces

The inspected ProjectInfinity-X/system_core branch-17 reference source has this sequence:

1. `LoadBootScripts()` parses the primary system init.rc, then the normal init directories.
2. At EOF, `Parser::ParseData()` ends the current section before calling section parsers' `EndFile()` handlers.
3. `ActionParser::EndSection()` registers the completed action. `ImportParser::EndFile()` then parses deferred imports.
4. Only after boot scripts are loaded does second-stage init queue `early-init`. `ActionManager` executes matching actions in registration order.

Consequently a complete appended action would be registered before vendor actions reached through those deferred imports/later directories. It is not inherently too late merely because it is appended at EOF. These reference sources are saved with hashes; their mutable branch snapshot is not represented as the exact historical compiled init revision. Source ordering is conditional on full delivery and the actual import path. The current failure needs neither assumption: **zero custom bytes means no test action was parsed or queued at all**.

The stock ROM's verified `init.qti.kernel.rc` starts vendor.modprobe on early-init. It uses the stock modules.blocklist and loads camera. No test helper execution or bind attempt preceded it in this failed run. PID 1 has no blocklist submount and sees stock blocklist hash `1fd4e3e5e5a13920c2fa75f0cdb7149869ceef6c194f2516ce977c0e492cb706`; the staged replacement hashes to `62c0bc30d1bb13b20b3088632c6668f59e7dad38e72e76b68851a768a7db5aec`.

PID 1 and the diagnostic shell currently share mount namespace `mnt:[4026532493]`. The exited loader's historical namespace cannot be recovered from this snapshot. Future exec-child mount propagation/permissions remain untested, but cannot explain an action that was never delivered. No nsenter, trigger change or SELinux relaxation is justified. Absence of early-guard logcat text is corroborative only; the byte-delivery failure is the decisive evidence.

## Actual helper and metadata transaction state

The metadata copies match the reviewed host files exactly:

- early-guard.sh: `28662c1eaa39d4bde5210f3d4cb51d72feeba2b9fa8a7be9f2a4418d2ed2c970`.
- metadata-transaction.sh: `2d6cfc26751eeb6ce5ea86aaa8298c498e1a19778d643ba885d1b730a2bb806d`.
- one-shot.rc: `48b07a40758e91fb16303d1aea20fb7062256a6bac60cbba835d61ddbd104364`, 233 bytes, valid early-init exec template.

The helper's control flow is restore → sync → blocklist integrity check → bind. Its restore would retire the owned active RC to `retired.rc` and link the saved original back to the watchdog pathname before mounting. Observed state instead:

| Path | Observed state |
| --- | --- |
| active watchdog modules.rc | **66305:293**, empty, root:root 0666, metadata_file label, correct empty hash |
| saved original.rc | **66305:286**, unchanged empty content/required attributes, one link |
| original.identity | **66305:286** |
| one-shot.rc | private inode 290, one link; no active watchdog link |
| retired.rc | **absent** |
| fallback /metadata/ksu/modules.rc | **absent** |
| PID 1 blocklist bind | **absent** |

This is not successful inode restoration by the helper. It is consistent with normal ksud post-fs-data regeneration replacing the guard with a new empty inode. Pinned `regenerate_preinit_rc()` creates a temp file, collects current custom RCs, and renames it onto modules.rc even when the result is empty, using the observed mode/label. The built-in post-fs-data event survived and was observed. There is no per-inode writer audit trail, so the exact creator PID of inode 293 is inferred from this code/state, not directly logged.

**No persistent active test hook remains in the captured and user-confirmed state.** The watchdog file is empty, fallback absent, and the private one-shot file is not a path that KernelSU reads automatically. Its presence under the private state directory is inert. The passive module has no initrc directory or automatic boot entrypoint. Stock camera has the same build-ID note hash as before installation. No experimental module was loaded.

## Smallest justified correction — kernel-side, not deployed

Only fix the **SUSFS fstat callback**: load/cache the custom RC before reporting virtual init.rc size, then include both built-in and cached custom lengths with checked arithmetic. Keep the existing helper's no-custom-RC switch, identity filtering, credentials, once-only snapshot, and balanced file reference handling.

`ksu-susfs-initrc-size.DRAFT.patch` expresses that correction against the pinned KernelSU file. It passes `git apply --check` on an isolated source copy. It was **not kernel-compiled, integrated into CI, applied to the original checkout, flashed, or installed**. The camera patch and candidate bytes are unchanged. A corrected kernel would require a separately authorized build/boot and authentic compatibility validation; a new camera.ko build cannot repair this hook. No safe userspace-only workaround is established for the current constraints.

The specific future diagnostic expectation is stat total 59,015, complete custom append of 233 bytes, then actual helper execution/restoration before vendor.modprobe. Only after delivery is fixed would testing of exec policy, mount namespace propagation and loader order become meaningful. This is a root-cause hypothesis validated offline, not a request to perform another experimental boot now.

## Rollback and required state before any retest

Current stock behavior needs no emergency change: active RC already has the expected empty content/owner/mode/label, fallback is absent and there is no bind to remove. **Leave the backup and private state intact. Do not delete them to get past the existing-state arming gate.** No reboot or driver unload is needed for this investigation.

At a later explicitly write-authorized cleanup step, invoke the existing reviewed rollback script (normal KernelSU root or OrangeFox root). In this exact state it preserves equivalent active inode 293, validates/retains saved original 286 and its identity, and disables only the passive test module. It does not overwrite the active empty file just to recover the earlier inode. The new host regression executes this exact no-retired-file scenario twice and verifies both inodes/content/xattrs are preserved; all **58 host unit tests pass**. These fixture ownership/SELinux adapters do not replace the user's already completed recovery rehearsal.

The unchanged [OrangeFox rollback](DEVICE-ROLLBACK.md) and independent host recovery kit remain valid even if userdata is unavailable. Keep both rollback-recovery.sh and metadata-transaction.sh together. Run no Manager mutation/refresh or broad metadata deletion to clean this up. All cleanup commands below are **proposals only, not executed**, and require approval for phone writes:

```bash
adb shell "su -c 'sh /data/adb/modules/marble-camera-kmd-test/rollback-recovery.sh'"
```

Before any separately approved retest, require: empty root:root 0666 metadata_file watchdog RC; absent fallback/custom active hook; original backup matching its identity; no PID 1 blocklist bind; stock camera still resident and normal vendor completion; preserved evidence; matching reviewed recovery kit; and a specifically validated correction for this kernel delivery defect. A cleanup disable marker is recommended while inactive and must not be removed without the next approved test plan. The retained state directory intentionally still prevents the old arming script from running. A future approved plan must explicitly preserve/archive that evidence and define fresh staging; do not remove the directory as a workaround. Do not run the old held arming/reboot instructions on this kernel.

No additional device command is needed to establish this cause. Evidence, source snapshots, reader binary hashes, disassembly, reproduction output and private raw logs are retained under `results/device-test/guard-failure-1/`; raw properties/logcat and ROM binaries are not committed or published.
