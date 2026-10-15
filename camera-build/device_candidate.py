#!/usr/bin/env python3
"""Runner-only experimental KMD candidate; no device writes or install packaging."""
import argparse
import json
import os
from pathlib import Path
import shutil
import urllib.error
import urllib.request
import zipfile

import ci
import codegen_experiment as probe
from audit import compare, cfi_symbol_surface
from cfi_pointers import pointer_inventory
from elf_probe import Elf
from module_inventory import inventory

RUN = 38701930741
RUN_SHA = "10ce3d0d89d17de0f7c8853a15acda6ad9822172"
ARTIFACT_ID = 11957367080
ARCHIVE_SHA = "96ba0a6d7851c4480c3f61f55674fa939b65653cfbe792a52f41cb11387e37dc"
BASELINE_SHA = "edf7665286ceb2ad1e2d932f2d4fb2f9cc74785185247212075558c020b0ec3b"
PATCH_SHA = "cbc37f8d259b096cb7625e03e71c4593f4232d5b6d80e84df0449d53f8465d7d"
REFERENCE = ci.WORK / "device-reference"
DEST = ci.OUT / "device-candidate"
SOURCE = ci.WORK / "modules-patched"
METADATA = ["elf_type", "machine", "compiler", "modinfo", "imports_crc", "undefined",
            "exports", "absolute_export_crc", "module_struct_size", "signature_trailer"]
BASELINE_REQUIRED = METADATA + ["cfi_symbols", "relocation_properties",
                               "cfi_type_dispatch_and_targets", "cfi_published_targets"]
DIAGNOSTIC_ONLY = {"allocatable_sections", "compiled_source_equivalence"}
PATCHED_REQUIRED = METADATA + ["cfi_symbols", "cfi_published_targets", "supported_runtime_relocations",
                               "native_cfi_failure_body", "arm64_security_properties", "cfi_type_target_groups",
                               "kmd_patch_compiled"]


def baseline_eligible(checks):
    return (set(checks) == set(BASELINE_REQUIRED) | DIAGNOSTIC_ONLY
            and all(checks.get(name) is True for name in BASELINE_REQUIRED))


def baseline_path():
    return REFERENCE / "full-diagnostic/camera.ko"


def inputs():
    # Retain the authenticated real kernel/MMRM tables from the first build.
    probe.inputs()
    token = os.environ["GITHUB_TOKEN"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    base = "https://api.github.com/repos/bigorbi/KernelSU-Next-marble"

    def api(path):
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=60) as r:
            return json.load(r)

    run = api(f"/actions/runs/{RUN}")
    if run["head_sha"] != RUN_SHA or run["conclusion"] != "success" or run["run_attempt"] != 1:
        raise ValueError("Corrected FullLTO run provenance mismatch")
    metadata = api(f"/actions/artifacts/{ARTIFACT_ID}")
    if (metadata["expired"] or metadata["workflow_run"]["id"] != RUN
            or metadata.get("digest") != "sha256:" + ARCHIVE_SHA):
        raise ValueError("Corrected baseline artifact provenance/expiry mismatch")
    request = urllib.request.Request(base + f"/actions/artifacts/{ARTIFACT_ID}/zip", headers=headers)
    try:
        urllib.request.build_opener(ci.NoRedirect).open(request, timeout=60)
        raise ValueError("Expected signed artifact redirect")
    except urllib.error.HTTPError as error:
        if error.code != 302:
            raise
        location = error.headers["Location"]
    if not location.startswith("https://"):
        raise ValueError("Unsafe redirect")
    archive = ci.WORK / "corrected-full.zip"
    with urllib.request.urlopen(location, timeout=60) as r, archive.open("wb") as w:
        shutil.copyfileobj(r, w)
    if ci.digest(archive) != ARCHIVE_SHA:
        raise ValueError("Corrected FullLTO archive hash mismatch")
    REFERENCE.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        if sum(i.file_size for i in zipped.infolist()) > 256 * 1024**2:
            raise ValueError("Unexpected artifact size")
        for i in zipped.infolist():
            path = Path(i.filename)
            if path.is_absolute() or ".." in path.parts or (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe artifact entry")
        zipped.extractall(REFERENCE)
    archive.unlink()
    if ci.digest(baseline_path()) != BASELINE_SHA:
        raise ValueError("Corrected FullLTO baseline SHA mismatch")
    flags = json.loads((REFERENCE / "full-flags.json").read_text())
    if flags["explicit_CC_FLAGS_LTO"] != probe.FULL_FLAGS or flags["source_changed"]:
        raise ValueError("Unexpected baseline compiler flags/source")


def reference():
    report = compare(baseline_path(), json.loads((ci.ROOT / "original-abi.json").read_text()),
                     json.loads((ci.ROOT / "original-audit.json").read_text()))
    report.update(experimental_device_test_eligible=baseline_eligible(report["checks"]),
                  status="EXPERIMENTAL_REFERENCE_AUDITED", gate_passed=False,
                  release_gate_passed=False, installation_approved=False,
                  policy="User accepts section/code differences as experimental risk; ABI and CFI remain required")
    ci.write("device-reference-audit.json", report)
    ci.write("compatibility.json", report)
    if not report["experimental_device_test_eligible"]:
        raise ValueError("Corrected FullLTO baseline fails required ABI/CFI checks")


def prepare():
    if not ci.read("device-reference-audit.json")["experimental_device_test_eligible"]:
        raise ValueError("Reference not verified")
    probe.prepare()
    recorded = json.loads((REFERENCE / "sources.json").read_text())
    current = ci.read("sources.json")
    for key in ["kernel_sha", "modules_sha", "manager_sha", "susfs_sha", "susfs_patch_sha256"]:
        if recorded[key] != current[key]:
            raise ValueError("Source mismatch with corrected baseline: " + key)
    helper = ci.KERNEL / "scripts/generate_cfi_kallsyms.pl"
    if ci.digest(helper) != "a4a8eb23cdbca209fb8846d153e7cc03a5d438cd239c0cf7852f2206de25164e":
        raise ValueError("Unexpected unmodified CFI helper")
    for args in [["--check"], []]:
        ci.run(["git", "-C", ci.KERNEL, "apply", *args, ci.ROOT / "cfi-bti-parser.patch"], "device-cfi-helper")
    if ci.digest(ci.ROOT / "camera-fix.patch") != PATCH_SHA:
        raise ValueError("Reviewed KMD patch changed")
    ci.run(["git", "clone", "--shared", "--no-checkout", ci.MODULES, SOURCE], "device-source")
    ci.run(["git", "-C", SOURCE, "checkout", "--detach", ci.MODULES_SHA], "device-source")
    for args in [["--check"], []]:
        ci.run(["git", "-C", SOURCE, "apply", *args, ci.ROOT / "camera-fix.patch"], "device-kmd-patch")
    ci.run(["python3", ci.ROOT / "tests/run_tests.py"], "device-kmd-offline-tests")
    (ci.REPORTS / "device-source.patch").write_text(ci.git(SOURCE, "diff", "HEAD") + "\n")


def build():
    ci.write("device-status.json", {"patched": "BUILDING", "installation_approved": False})
    flags = probe.full_lto_flags(ci.config_values(ci.KOUT / ".config"))
    before = ci.digest(ci.KOUT / ".config")
    ci.write("device-build-inputs.json", {"CC_FLAGS_LTO": flags, "configuration_sha256": before,
             "patch_sha256": PATCH_SHA, "source_sha": ci.MODULES_SHA,
             "cfi_helper_sha256": ci.digest(ci.KERNEL / "scripts/generate_cfi_kallsyms.pl"),
             "kernel_symvers_sha256": ci.digest(ci.KOUT / "Module.symvers"),
             "mmrm_symvers_sha256": ci.digest(ci.MODULES / "qcom/opensource/mmrm-driver/Module.symvers")})
    camera = SOURCE / "qcom/opensource/camera-kernel"
    ci.run(ci.make_args(True) + ["M=" + str(camera), "CAMERA_KERNEL_ROOT=" + str(camera),
           "KERNEL_ROOT=" + str(ci.KERNEL), "MODNAME=camera", "TARGET_PRODUCT=marble",
           "KBUILD_EXTRA_SYMBOLS=" + str(ci.MODULES / "qcom/opensource/mmrm-driver/Module.symvers"),
           "CC_FLAGS_LTO=" + flags, "V=1", "modules"], "device-full-build", probe.build_env("android-r563880c"))
    if ci.digest(ci.KOUT / ".config") != before:
        raise ValueError("Camera compilation changed boot configuration")
    tables = {"kernel": ci.KOUT / "Module.symvers",
              "mmrm": ci.MODULES / "qcom/opensource/mmrm-driver/Module.symvers",
              "camera": camera / "Module.symvers"}
    for name, path in tables.items():
        shutil.copyfile(path, ci.REPORTS / (name + "-Module.symvers"))
    ci.write("device-symbol-tables.json", {name: {"sha256": ci.digest(path), "size": path.stat().st_size}
                                          for name, path in tables.items()})
    ci.write("device-status.json", {"patched": "BUILT_UNVERIFIED", "installation_approved": False})


def semantic_cfi(path):
    # Actual ARM64 dispatcher execution, not a symbol-name-only substitute.
    from emulate_cfi import emulate, targets
    elf, groups, accepts = emulate(path)
    ids = [int(name, 16) for name in groups]
    negatives = 0
    for i, (name, group) in enumerate(groups.items()):
        address = group["addresses"][0]
        if (accepts(int(name, 16), address + 1) or accepts(int(name, 16), address - 1)
                or accepts(ids[(i + 1) % len(ids)], address)):
            raise ValueError("CFI wrong-type/alignment rejection failed")
        negatives += 3
    return targets(elf, groups), negatives


def verify():
    path = SOURCE / "qcom/opensource/camera-kernel/camera.ko"
    baseline, patched = inventory(baseline_path()), inventory(path)
    checks = {name: baseline[name] == patched[name] for name in METADATA}
    checks["cfi_symbols"] = cfi_symbol_surface(baseline["cfi_symbol_records"]) == cfi_symbol_surface(patched["cfi_symbol_records"])
    a, b = pointer_inventory(baseline_path()), pointer_inventory(path)
    checks["cfi_published_targets"] = (not b["invalid_published_pointers"] and not b["unattributed_fields"]
                                       and a["publications"] == b["publications"]
                                       and a["cfi_entry_count"] == b["cfi_entry_count"])
    be, pe = Elf(baseline_path(), allocated_only=True), Elf(path, allocated_only=True)
    kinds = lambda e: {kind for rows in e.rel.values() for _, kind, _, _ in rows}
    checks["supported_runtime_relocations"] = kinds(pe) <= kinds(be)
    checks["native_cfi_failure_body"] = be.body("__cfi_check_fail") == pe.body("__cfi_check_fail")
    checks["kmd_patch_compiled"] = be.body("cam_packet_util_get_kmd_buffer") != pe.body("cam_packet_util_get_kmd_buffer")
    section_bytes = lambda elf, name: elf.d[elf.ss[elf.names.index(name)][4]:
        elf.ss[elf.names.index(name)][4] + elf.ss[elf.names.index(name)][5]]
    checks["arm64_security_properties"] = section_bytes(be, ".note.gnu.property") == section_bytes(pe, ".note.gnu.property")
    bt, bn = semantic_cfi(baseline_path())
    pt, pn = semantic_cfi(path)
    checks["cfi_type_target_groups"] = bt == pt
    report = {"checks": checks, "binary_safety_checks_pass": all(checks.values()),
              "baseline_sha256": BASELINE_SHA, "patched_sha256": ci.digest(path),
              "patch_sha256": PATCH_SHA, "baseline_negative_cfi_tests": bn, "patched_negative_cfi_tests": pn,
              "release_gate_passed": False, "installation_approved": False,
              "remaining_device_gates": ["early stock-load suppression", "all dependencies loaded", "OrangeFox rollback rehearsal"],
              "compiled_code_and_section_differences": "Documented experimental risk, per user instruction"}
    ci.write("device-candidate-audit.json", report)
    if not all(checks.values()):
        raise ValueError("Patched camera ABI/CFI safety checks failed")
    DEST.mkdir()
    shutil.copyfile(path, DEST / "camera.ko")
    shutil.copyfile(ci.ROOT / "camera-fix.patch", DEST / "camera-fix.patch")
    shutil.copyfile(ci.REPORTS / "device-candidate-audit.json", DEST / "audit.json")
    shutil.copyfile(ci.REPORTS / "sources.json", DEST / "sources.json")
    shutil.copyfile(ci.REPORTS / "device-build-inputs.json", DEST / "build-inputs.json")
    dependencies = next(row.split("=", 1)[1] for row in patched["modinfo"] if row.startswith("depends="))
    (DEST / "required-modules.txt").write_text(''.join(name.replace('-', '_') + "\n" for name in dependencies.split(',') if name))
    (DEST / "kernel.release").write_text(ci.RELEASE + "\n")
    (DEST / "ABI-CFI-PASS").write_text("experimental binary checks passed; device loading remains conditional\n")
    if ".note.gnu.build-id" in pe.names:
        (DEST / "camera-build-id.note").write_bytes(section_bytes(pe, ".note.gnu.build-id"))
    (DEST / "CLASSIFICATION.txt").write_text("EXPERIMENTAL MANUAL-TEST CANDIDATE. NOT INSTALLED OR DEVICE-VALIDATED. NO AUTO-LOADER.\n")
    (DEST / "SHA256SUMS").write_text(''.join(ci.digest(p) + "  " + p.name + "\n" for p in sorted(DEST.iterdir())))
    ci.write("device-status.json", {"patched": "BUILT_SAFETY_CHECKED_EXPERIMENTAL", "installation_approved": False})


def finalize():
    for folder in [ci.LOGS, ci.REPORTS]:
        folder.mkdir(parents=True, exist_ok=True)
    status_path = ci.REPORTS / "device-status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {"patched": "NOT_BUILT"}
    failure_path = ci.REPORTS / "device-failure.json"
    summary = {"status": status, "failure": json.loads(failure_path.read_text()) if failure_path.exists() else None,
               "baseline_run": RUN, "baseline_sha256": BASELINE_SHA,
               "release_gate_passed": False, "installation_approved": False,
               "kernel_mmrm_tables": "Authenticated reuse from run 38603388362; not rebuilt in this run"}
    ci.write("device-summary.json", summary)
    for folder in [ci.LOGS, ci.REPORTS]:
        (folder / "SHA256SUMS").write_text(''.join(ci.digest(p) + "  " + str(p.relative_to(folder)) + "\n"
            for p in sorted(folder.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS'))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], 'a') as stream:
            stream.write("## Experimental camera candidate\n\nStatus: " + status['patched'] +
                         ". Release approval: false. Device installation: not performed.\n")


STAGES = {"inputs": inputs, "reference": reference, "prepare": prepare, "build": build, "verify": verify,
          "finalize": finalize}
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({"stage": args.stage, "network_or_compilation": False, "phone_writes": False,
                          "release_gate_can_open": False, "baseline_run": RUN}))
    elif os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Candidate stages may execute only on GitHub Actions")
    else:
        try:
            STAGES[args.stage]()
        except Exception as error:
            message = "Authenticated artifact request failed" if isinstance(error, urllib.error.URLError) else str(error)
            ci.write("device-failure.json", {"stage": args.stage, "error": message, "installation_approved": False})
            raise SystemExit(message) from None
