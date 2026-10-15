# Camera code-generation investigation — 2026-10-15

**Current result: the final-link CFI correction succeeded in [run 38626370150](RUN-38626370150.md).** FullLTO reproduces much more of the ROM, but an erroneous custom trap-handler flag confounded that experiment. The flag correction and regression tests are prepared locally; no further run is authorized or started, and no installation candidate exists. The linked report records the completed run, current blockers and next experiment.

The pre-experiment analysis below is retained from commit `627a7f7`, since published and used for that run. It supersedes the CFI interpretation in [the first audit report](RUN-38603388362.md), while retaining its raw measurements, and reuses the authenticated artifacts from [run 38603388362](https://github.com/bigorbi/KernelSU-Next-marble/actions/runs/38603388362). Statements below about proposed execution describe the historical plan; the completed results are in the newer report.

## Confirmed causes

### Published CFI callback pointers: an exact Kbuild/disassembler compatibility defect

Both modules publish **714** pointers into their CFI jump tables. All original ROM pointers target valid BTI entry starts. **41 baseline pointers, covering 15 functions, point four bytes past the valid entry**, directly at its B instruction. They are in ISP, ICP, node and custom context state/operation tables.

Example: `.data:cam_isp_ctx_activated_state_machine+96` publishes `__cam_isp_ctx_apply_req_in_sof`.

| Property | Original ROM | Cloud baseline |
| --- | --- | --- |
| Symbol's .text offset | 0x19b280 | 0x17dc74 |
| Relocation addend | 0 | 0 |
| Actual BTI entry offset | 0x19b280 | 0x17dc70 |
| Expected type ID | 5fd778e6297eedac | 5fd778e6297eedac |
| Actual dispatcher accepts published address | Yes | **No** |
| Actual dispatcher accepts address minus four | No | **Yes** |

Independent Unicorn execution confirms **all 41** rejected published addresses and acceptance of their preceding BTI entry for the corresponding original type ID. This is not a hypothetical name-comparison problem. The baseline would fail CFI if these pointers are invoked through checked indirect calls; it also skips the required BTI landing instruction. It was never installed or executed on the phone.

The exact cause is the pinned kernel's [`scripts/generate_cfi_kallsyms.pl`](https://github.com/aosp-pablo/android_kernel_xiaomi_sm8450/blob/5c59cd9a314704d30f3f66004183426da23410e1/scripts/generate_cfi_kallsyms.pl), source SHA-256 `a4a8eb23cdbca209fb8846d153e7cc03a5d438cd239c0cf7852f2206de25164e`. Its `is_hint()` recognizes disassembly spelling `hint #...` but not `bti c`. Consequently, when the displayed label differs from the expected callback alias, the helper can assign or redefine that alias at the B address instead of the preceding BTI address. It does not independently verify whether an alias already exists elsewhere in the symbol table.

[`Makefile.modfinal`](https://github.com/aosp-pablo/android_kernel_xiaomi_sm8450/blob/5c59cd9a314704d30f3f66004183426da23410e1/scripts/Makefile.modfinal) invokes this helper after the initial final link, then runs LLD again with its generated linker script. The synthesized aliases are GLOBAL/HIDDEN/NOTYPE, explaining the 21 unusual baseline symbols identified earlier. Of those 21 aliases, 15 are referenced by the 41 published data pointers. This is an independently reproduced build-helper defect exposed by modern disassembly spelling; the baseline table instructions themselves are valid. The first run did not upload its generated `camera.lds` or prelink objects, so the proposed control/relink captures that intermediate output to verify the assignment chain for every actual alias rather than attributing it to an undocumented compiler change.

The ARM64 loader computes ABS64 values as `symbol.st_value + addend`; `simplify_symbols()` adds the section base. Its CFI setup specially redirects module init/exit, not arbitrary camera operation-table callbacks. Nothing subtracts four from these pointers. `.text` is page-aligned, so placing the module elsewhere does not repair the mismatch.

A small independently assembled ARM64 ELF fixture replays the actual pinned Perl helper:

| Same fixture, helper/disassembly | Generated callback alias |
| --- | ---: |
| Original helper, `bti c` | 12 — wrong B address |
| Original helper, equivalent `hint #34` | 8 — correct BTI address |
| Reviewed helper patch, `bti c` | 8 |
| Reviewed helper patch, `hint #34` | 8 |

`cfi-bti-parser.patch` extends that one parser expression to recognize `bti c`; it preserves the legacy spelling. Tests also prove that the pointer audit rejects the synthesized wrong fixture address and accepts the correct one. The patch is provided but **not applied to the normal build inputs** in this change. It is applied only in the proposed diagnostic experiment, after its control build.

The audit now additionally requires **`cfi_published_targets`**: valid entry starts, attributable data fields, and the original field-to-function publication map. It fails on this baseline. The same requirement strengthens the future patched-module comparison. Existing CRC, CFI fingerprint, code fingerprint, configuration, lifetime and exploratory gates remain intact. The previous internal-name normalization never establishes address validity.

### .text: exact byte accounting, with strong evidence of different inlining

| Byte category | ROM | Baseline | Baseline minus ROM |
| --- | ---: | ---: | ---: |
| Function-body union | 1653964 | 1533704 | -120260 |
| Actual BTI/B entries | 8752 | 8752 | 0 |
| Padding/unattributed tail bytes | 22004 | 21912 | -92 |
| **Total .text** | **1684720** | **1564368** | **-120352** |

Actual entries are counted from BTI/B instructions and JUMP26 relocations, not the erroneous aliases. Counting those aliases initially misallocated 84 bytes; the corrected accounting above handles overlapping symbols without double counting.

The largest confirmed inlining effect is register I/O tracing:

| Quantity | ROM | Baseline |
| --- | ---: | ---: |
| MMIO-write static trace sites | 527 | 8 |
| CALL26 references to cam_io_w | 3 | 15 |
| CALL26 references to cam_io_w_mb | 93 | 586 |
| cam_io_w body size | 212 | 212 |
| cam_io_w_mb body size | 216 | 216 |
| cam_io_r body size | 272 | 272 |
| cam_io_r_mb body size | 296 | 296 |

All four I/O wrapper bodies match under the stricter diagnostic opcode/reference comparison. Both configurations enable MMIO tracing. The ROM has many embedded copies of their trace checks, while the baseline calls shared wrappers far more often: **505 additional calls** to write wrappers, **519 fewer embedded write trace sites**. This directly establishes different cross-function expansion, not a disabled tracing option.

Among uniquely paired changed functions, 122 have different MMIO-write site counts and collectively shrink by 68,148 bytes. For example, `cam_ife_csid_ver2_start` shrinks 3,644 bytes and loses 30 local write trace sites; `__cam_csiphy_get_preamble_status` shrinks 1,528 bytes and loses 13. Other inlining, dead-branch elimination, register allocation and cloning differences remain. These measurements explain the mechanism and substantial regions, not every changed instruction.

### Allocated data: specific causes and remaining limits

- **.bss −4 bytes:** named object-size inventories match, and object storage totals 3,461,314 bytes in both. The four-byte difference is padding/unattributed alignment space, not a demonstrated changed object ABI.
- **.data +40 bytes:** the entire difference is an extra 40-byte `__cam_req_mgr_notify_error_on_link._rs.191` rate-limit state alongside `_rs`. Both baseline copies have executable references; the ROM has one copy. This accompanies a cloned private notification function. It is compiler-pipeline evidence, but independent rate-limit state could affect logging behavior. It is not classified as universally harmless.
- **.rodata +1,440 bytes:** eight embedded `__FILE__` paths use the relative ROM build path `../sm8450-modules/...` versus the runner's absolute path. Their stored lengths rise from 712 to 1,280 bytes: **568 bytes explained exactly**. Named object storage grows by 99 bytes; it includes additional small string/device-name objects. A second identical three-pointer table adds 24 bytes outside those named ranges. The remaining 749 bytes comprise pool/constant/dead-path differences not fully isolated. No path rewriting or blanket section-size exception was introduced.

Build paths are genuine compiler inputs. Matching the original relative path later should use a documented build directory/M path; it should not rewrite metadata or claim binary identity by editing `.rodata`.

### Four runtime relocation differences: structural classification

1. **.rela__jump_table:** exactly 563 fewer entries and therefore 1,126 fewer PREL32 plus 563 fewer PREL64 relocations. The key differences account for every entry:
   - rwmmio_write: −519;
   - arm64_const_caps_ready: −20;
   - cpu_hwcap_keys+81: −20;
   - rwmmio_read and rwmmio_post_read: −2 each.
   Camera-event tracepoint counts are unchanged. This is different placement/duplication of inline kernel instrumentation, not a new unsupported relocation ABI.
2. **.rela.altinstructions:** 71 fewer alternative sites, exactly 142 fewer PREL32 relocations and 852 fewer table bytes. Feature 59 (LDAPR) loses 69 sites; feature 1 (device-load acquire workaround) loses two. Features 42, 10 and 11 and their four-byte original/replacement lengths are unchanged. No new CPU feature class appears. Site removal is consistent with inlining/cloning differences; complete path equivalence is not proved.
3. **.rela.rodata:** the three extra ABS64 relocations all point back into `.rodata`. They form an additional copy of the string-pointer table `UNCHANGED`, `INCREASE`, `DECREASE` (two copies in the baseline versus one in the ROM). Text/data/BSS/provider target-category counts otherwise match. This specific count difference is read-only constant-table duplication, not three new callback targets.
4. **.rela.text:** relocation kinds are unchanged; counts differ with instruction references, wrapper calls and embedded trace code. CALL26 rises in some shared-wrapper paths while other calls disappear; overall ROM/baseline totals are 21,293/20,431. JUMP26 remains exactly 1,094, matching the CFI table. Raw counts do not establish call-target or behavior equivalence; normalized per-function references remain part of diagnostics, and the strict relocation gate stays closed.

The newly confirmed bad callback addresses are in allocated `.data` ABS64 relocations whose **counts already matched**. This demonstrates why equal relocation histograms alone are insufficient.

## Compiler configuration and source provenance: what remains unresolved

The compiler `.comment` is identical: Android Clang 21.0.0 r563880c/build 14054515, source `5e96669f06077099aa41290cdb4c5e6fa0f59349`. It contains no original LLD identity or full command line. The first run retained normal build commands, not complete per-object flags; subsequent builds use `V=1`.

The pinned kernel Makefile selects `-O2`, ThinLTO, `-fsplit-lto-unit`, cross-DSO CFI with noncanonical tables, trapping CFI failures, and a **cross-file import limit of 5**. ARM64 build rules enable `pac-ret+leaf+bti` and the running configuration enables shadow call stack. The ROM's CFI table uses the same 198 accepted type IDs and destination names, and its entry instructions include BTI. That confirms relevant security mechanisms, not every original optimization setting.

A **FullLTO original camera build** is the leading explanation for the inlining, private-symbol naming, constant folding and missing-alias differences. It remains an inference. ThinLTO with different import/internalization flags is another explanation; original optimization level/linker flags are not yet known. The [LLVM description of ThinLTO](https://clang.llvm.org/docs/ThinLTO.html) explains its separate per-module importing/backend pipeline. The historical Zois build was image-only and establishes only the boot-kernel environment, not the ROM camera compiler command.

Selected layout evidence agrees: 331 imported CRCs, exported inventory, module size, 714 callback field offsets/names and all named BSS object sizes. KMD and packet-validation bodies match after relocation handling; the four I/O wrappers also match. No specific incompatible internal structure layout has been demonstrated. This does not uniquely identify all original headers.

`deep_compare.py` retains relocation opcodes/register operands, resolves symbol/string identities, accounts for LLD-resolved branches, and refuses to pair duplicate normalized names arbitrarily. It is diagnostic only and **does not replace the security gate**. Of 2,163 original and 2,167 baseline normalized body names, 1,135 unique pairs match without unresolved references, 1,008 differ and 19 names are ambiguous. Original-only `__camera_exit` is a plausible alias/optimization effect; the five baseline-only helpers are string/enum/outlining helpers.

Functions requiring controlled follow-up beyond the MMIO explanation include:

| Function | ROM bytes | Baseline bytes | Status |
| --- | ---: | ---: | --- |
| cam_sensor_i2c_command_parser | 7368 | 4056 | No local write-site delta; other inlining/DCE unresolved |
| cam_get_dt_power_setting_data | 4196 | 2008 | No local write-site delta; helper/branch optimization unresolved |
| cam_ife_hw_mgr_init | 6456 | 4948 | No local write-site delta; optimization/header/source cause unresolved |
| __cam_req_mgr_notify_error_on_link | private clone/state differences | two referenced states | Cloning and rate-limit behavior require review |

Several baseline-only cleanup/error strings are present in the pinned source; whole-program constant propagation can eliminate checked failure paths when arguments/returns are known. Their absence in the ROM is not by itself proof of an earlier source revision. **No function has yet been proved to require an enabled-source change rather than compiler settings.** Actual instruction differences are identified; their ultimate source/flag cause is still open.

The supported source pin remains `83ff8a6f43d2fd897a6ee262f5965d4a9d75f00a`. Its parent changes only disabled ISPV3 cases, so choosing that parent cannot resolve these differences or uniquely establish original provenance. No speculative source pin was substituted.

## Can a minimally patched driver preserve unrelated compiled functions?

The confirmed build-helper correction can be tested by repeating only the final module link, reusing every C object and `camera.lto.o`. It corrects generated CFI aliases through the normal build pipeline, with no vermagic/CRC override or protection removal. The experiment requires all compiled-object checksums to stay unchanged. This is not a camera code patch or an installation output.

For the KMD correction itself, changing either reviewed C source file under LTO may change inlining, cloning, register allocation and layout outside those functions. It is **not currently possible to promise that every unrelated compiled function stays unchanged**. After a compatible baseline and lifetime review exist, the patch must be built in isolation and every unrelated opcode/reference fingerprint difference reviewed. Splicing machine code, copying CRCs or disabling LTO/CFI to satisfy a comparison would not prove safety and is not proposed.

## Original cloud experiment plan — subsequently executed as run 38626370150

Workflow: `.github/workflows/camera-codegen-experiment.yml`; staged runner script: `camera-build/codegen_experiment.py`.

This is one sequential job, two genuine camera compilations and one final-link repeat. It performs **no full kernel or MMRM build**. It authenticates the existing run's baseline and symbol-table artifacts, reconstructs the same pinned manager/SUSFS source and configuration, runs `modules_prepare` to generate genuine headers/tools, and reuses the unmodified actual kernel/MMRM Module.symvers tables. A control prevents treating a failed environment reconstruction as an LTO result.

| Stage/hypothesis | Required observation |
| --- | --- |
| ThinLTO control using prepared headers and prior real tables | Same allocated sections, strict function fingerprints, runtime relocations, CRCs/metadata and 41 bad publications as the prior baseline. Otherwise stop before experiments. |
| BTI-aware generator, final-link repeat | Every compiled `.o` checksum unchanged; 714 valid publications matching ROM; no 41 off-by-four pointers. No camera C source changes. |
| FullLTO instead of ThinLTO, fixed generator, same Clang/config/source | Measure whether MMIO-write sites rise toward 527, wrapper calls fall toward 3/93, function-body bytes approach 1,653,964, duplicate rate-limit state/table differences disappear and unresolved functions converge. Full flags are explicit; the boot config remains honestly ThinLTO. |

The 568-byte absolute/relative source-path difference is expected to remain in the Full-only probe; it must be reported rather than hidden. FullLTO convergence would support a code-generation cause, not prove a unique historical source SHA. Failure to converge identifies exact function groups for the next targeted flag/header/source investigation; no automatic O3/source matrix is added.

The workflow is manual-only, read-only permissions plus Actions read access for the input artifacts, pinned actions/source revisions, two make/ThinLTO jobs, 8 GiB swap, and diagnostic uploads even on failure. It cannot open a release gate, apply the camera fix, create a candidate artifact or label a module installable. The original artifact inputs expire October 28 unless retained separately. Generated-header preparation/control may fail; that will be reported rather than falling back to a full kernel build.

Local validation: 23 tests pass, including actual pinned-helper replay, legacy/new disassembly, invalid published-pointer detection, opcode/register preservation and experiment safeguards. YAML, shell/Python syntax and dry runs pass. The original module's reference self-check passes all 16 conditions. This baseline fails the four prior conservative checks **plus the new confirmed `cfi_published_targets` condition**. Historical SUSFS attribution and request lifetime review remain independent blockers.

Pre-experiment results are retained locally under `results/codegen-investigation/`, including all 41 dynamic pointer tests, precise section/call/feature inventories and strict compatibility JSON. Public source files contain only scripts, a source patch, names, hashes and metadata; the original ROM module/instruction bytes are not committed. That analysis session performed no large local compilation, phone action, push or cloud dispatch. The later approved run's evidence is under `results/actions-run-38626370150/`.
