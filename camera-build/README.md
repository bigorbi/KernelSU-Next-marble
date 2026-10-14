# Camera baseline and gated correction

This workflow attempts a real ARM64 kernel/MMRM/camera build on GitHub-hosted Ubuntu. It never flashes a phone, installs modules, changes CRCs/vermagic, creates a flashable package, or runs on push/PR events. The original upstream build workflow is unchanged.

The pinned historical builder is used as a source reference, while the camera pipeline has its own independent scripts. The verifier and toolchain installer reuse the project's reconstruction logic, adapted to this repository layout. All substantial compilation happens on Actions; local `ci.py --dry-run` does not download, compile or write build output.

## Repository settings and starting a future approved run

1. Enable Actions on this fork under **Settings → Actions → General**, allowing the pinned `actions/checkout` and `actions/upload-artifact` actions. **Workflow permissions: Read repository contents** is sufficient. No repository write, deployment, attestation or OIDC permission is requested.
2. GitHub requires a workflow_dispatch workflow to be present on the default branch before registering it for manual runs. This implementation is intentionally pushed only to `camera-fix`. Set this fork's **default branch to camera-fix**, or separately authorize registering this workflow on the fork's default branch. Nothing is changed in Pablo's repository. No branch-setting change or dispatch is performed by this delivery.
3. Once running is approved, open **Actions → Camera baseline and gated correction → Run workflow** and select branch **camera-fix**.
4. For the first baseline attempt use `susfs_ref=gki-android12-5.10`, **exploratory=true**, **build_patched=false**, and an empty private reference. A full SUSFS SHA can be supplied for a repeatable exploratory run. The branch is resolved to an immutable SHA once and that SHA/patch checksum is recorded before integration.

One runner job, two make jobs, two kernel ThinLTO jobs, an 8 GiB swap file and a 300-minute timeout are used. The workflow removes unused preinstalled SDKs on the ephemeral runner, deletes downloaded/compiler Git objects once verified, and avoids unnecessary matrices, manager variants and debug builds. Logs and reports upload on failure; only modules actually produced are uploaded. There is no unverified candidate artifact.

## Pinned inputs

| Component | Input |
| --- | --- |
| Historical builder | `3577f1d72a7c00c9248882ccac70ac3f15a97baa` |
| Kernel | `aosp-pablo/android_kernel_xiaomi_sm8450@5c59cd9a314704d30f3f66004183426da23410e1` |
| Module sources | `aosp-pablo/android_kernel_xiaomi_sm8450-modules@83ff8a6f43d2fd897a6ee262f5965d4a9d75f00a` |
| KernelSU Next | `pershoot/KernelSU-Next@9a8d3f03db9bd6d51410a6fd732d1db273692ca6` |
| Kernel compiler | Upstream LLVM 22.1.8, SHA-256 `df0e1ecf16caf3489a272a5eea4eec9b0d82878f6477fa309504f918a0006384` |
| Module compiler | Official AOSP prebuilt commit `531a564ba5426707dee226eccb19f104a7af5a5f`, r563880c/build 14054515, complete Git tree/blob verification |
| Kernel release | Naturally generated `5.10.269-Zois-17-5c59cd` using actual branch context `17` |
| Configuration | Committed running-config.txt, matching compiler configuration and ThinLTO |

The [October 6 source log](https://github.com/aosp-pablo/KernelSU-Next-marble/actions/runs/37435765886) reported packaging version **33329** (full history count 3329) at 08:28:06. The actual Kbuild at 08:47:52 reported **33321**, tag **v3.4.0**, using its merge-base count. We supply `KSU_GIT_VERSION=3321`, `KSU_GIT_VERSION_VALID=1`, `KSU_GIT_TAG=v3.4.0`; Kbuild itself derives `30000+3321=33321`. The kernel build log is checked for those exact compiled values. The packaging helper is not run. We do not invent Git history or change module ABI metadata to disguise a different build.

The old workflow recorded a SUSFS branch instead of a SHA and completed before the phone's recorded build timestamp. That provenance remains unresolved. Exploratory mode permits a documented baseline attempt while retaining that limitation; a successful compilation is not a compatibility verdict.

## Stages and gates

The pipeline checks out the pinned builder/kernel/modules, verifies both compilers, integrates the pinned manager and resolved SUSFS tree, compares every enabled configuration value and the natural release, builds `vmlinux modules`, verifies genuine generated headers/Module.symvers, builds external MMRM, then compiles the **unmodified** camera baseline with Android Clang 21. External compilation must leave the generated kernel configuration untouched.

The original ROM module is not in this repository. `original-abi.json` contains only module metadata, dependencies, import/export CRCs, section properties and CFI symbol names. `original-audit.json` contains relocation statistics and one-way hashes of normalized function/CFI machine-code fingerprints derived locally from the SHA-verified original. It contains no instruction bytes. Reference SHA-256 is `a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1`.

Baseline comparisons cover every versioned import and export, undefined symbols, module struct layout, compiler/modinfo/dependencies, CFI symbol surface, allocatable section properties, relocation type counts, CFI type dispatch/targets and all defined function fingerprints. A rebuilt module hash is **not** expected to match the original. Conservative fingerprints may reject otherwise equivalent link layouts; their differences are reported rather than silently normalized away. Any mismatch closes the gate and fails the audit stage while uploading diagnostics.

Additional patch/release gates are explicit in `compatibility-policy.json`:

- Historical SUSFS SHA and attributable evidence must be committed after verification, and must match the selected source.
- The previously identified request error-path cleanup/lifetime review must be completed, with its corrective patch and review evidence incorporated into the complete reviewed patch as needed.
- Exploratory mode must be disabled; configuration/release, ABI, CFI and compiled source equivalence must all pass.

These policy fields deliberately remain unresolved. Checking `build_patched` cannot override them. The immediate supported operation is an exploratory **baseline** attempt. No build will be labelled release-compatible merely because it compiles.

Once all gates actually pass, the optional corrected build clones a fresh module checkout at the pinned commit, applies the complete reviewed patch, runs the existing malformed/boundary offline function suite on the runner, compiles a separate camera module, and checks its import/export CRCs, metadata and CFI symbol surface against the passed baseline. A changed ABI is not published as a candidate. Even a gated candidate still requires phone testing and installation review; no module is labelled safe for installation.

## Optional private binary comparison

No binary input is needed for the initial metadata/fingerprint audit. For an authenticated recheck, upload the original `camera.ko` as a workflow artifact in a **separate private repository** you control. Do not put the binary in this public fork or a public artifact.

Create a fine-grained secret **CAMERA_REFERENCE_TOKEN** in this fork, restricted to the private reference repository with **Actions: Read** and **Contents: Read**. Set the dispatch input `private_reference` to `owner/private-repo:artifact_id`. The input must identify an unexpired artifact ZIP containing exactly one file named `camera.ko` (directory prefixes allowed). The helper verifies that the repository is private, downloads through GitHub's authenticated artifact API, and checks the original SHA and audit provenance.

The token is exposed only to the comparison step. It is never logged or forwarded to the signed download host. The private module/archive stay under RUNNER_TEMP, outside every artifact path, and are removed before uploads. Neither its instruction bytes nor a disassembly are emitted. Do not use a public download URL or commit the binary to simplify this step.

## Artifacts and failure meanings

- **kernel-build-logs**: stage logs, compiler/config/build diagnostics and checksums, even after failure.
- **baseline-camera**: only if compilation produced camera.ko; explicitly UNVERIFIED until gate results are reviewed, with source manifest/checksum.
- **baseline-compatibility-report**: configuration, toolchain/source manifests, original-versus-baseline checks, actual generated kernel/MMRM/camera symbol tables and checksums, including NOT_RUN after early failure.
- **patched-camera**: only after every gate passes and patched ABI checks succeed, with source manifest/checksum and candidate-only classification.

Missing inputs, patch rejects, configuration differences, compiler/linker errors, memory exhaustion, modpost failures, and ABI/fingerprint mismatches are actual failures. They do not count as successful module builds. The original camera source commit is a supported candidate, not proven exact ROM provenance; compiled source comparison may expose that difference. Logs and reports identify the failing stage.

## Local validation only

```sh
python3 camera-build/validate.py
python3 -m unittest discover -s camera-build/tests -p 'test_ci.py'
python3 camera-build/ci.py kernel --dry-run
```

These commands validate YAML structure/pinned actions, Python syntax and fail-closed gate behavior. They perform no ARM64 compilation and do not dispatch Actions. Actual compilation is untested until an approved runner execution. The unresolved historical SUSFS provenance and lifetime review cannot be solved merely by running the build in the cloud.
