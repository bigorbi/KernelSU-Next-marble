#!/usr/bin/env python3
"""Runner-only diagnostic probe: prepare headers, reuse real tables, build two camera variants.

No kernel/MMRM rebuild, camera patch, candidate gate or installation path exists.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
import ci
from audit import audit, compare
from module_inventory import inventory
from cfi_pointers import pointer_inventory

RUN = 38603388362
RUN_SHA = "009a9d3da3e4d973b41843a9fb9cd7f131bc1d58"
SUSFS_SHA = "ca7093a1a26f7911987110c34a1352d74cfb1d09"
ARTIFACTS = {
    "reports": (
        11916212764,
        "09bbe435f27d6220b6a43b9113454a845baf2ee10689bf33c871fa7b49e67ff0",
    ),
    "baseline": (
        11916777612,
        "eb2098e1a25e73dfdbda4b27cd87e5d9750223f5f6a6c4a5182fc893e5151c23",
    ),
}
TABLES = {
    "kernel-Module.symvers": "1e772632d2d2b37ba92388df4e502aed3146e1410ac9d18b51f51dc17daac03f",
    "mmrm-Module.symvers": "a983328d6b99a1e0eedcbafdc9aade443355ab573bcd372b88e164ab30c108a7",
}
BASELINE_SHA = "5d8e4c2e9a9b72c058cfae0473f561734bad3382550ea130c33514f3efd85d2c"
PROBE = ci.OUT / "codegen-probe"
INPUT = ci.WORK / "probe-input"
FULL_FLAGS = (
    "-flto -fvisibility=default -fsanitize=cfi -fsanitize-cfi-cross-dso "
    "-fno-sanitize-cfi-canonical-jump-tables -fno-sanitize-blacklist"
)


def full_lto_flags(config):
    # The pinned Makefile omits a custom trap handler under CONFIG_UBSAN_TRAP.
    # Keep native trapping CFI and reject configurations outside this experiment.
    required = {
        "CONFIG_LTO": "y",
        "CONFIG_LTO_CLANG": "y",
        "CONFIG_LTO_CLANG_THIN": "y",
        "CONFIG_CFI_CLANG": "y",
        "CONFIG_CFI_PERMISSIVE": "n",
        "CONFIG_UBSAN_TRAP": "y",
    }
    for key, value in required.items():
        if config.get(key) != value:
            raise ValueError(f"FullLTO probe requires {key}={value}")
    return FULL_FLAGS


def write(name, value):
    PROBE.mkdir(parents=True, exist_ok=True)
    (PROBE / name).write_text(json.dumps(value, indent=2) + "\n")


def build_env(toolchain):
    env = ci.environment(toolchain)
    env.pop("GITHUB_TOKEN", None)
    return env


def inputs():
    if os.environ.get("GITHUB_REPOSITORY") != "bigorbi/KernelSU-Next-marble":
        raise ValueError("Probe input must come from the verified fork")
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = "https://api.github.com/repos/bigorbi/KernelSU-Next-marble"

    def api(path):
        with urllib.request.urlopen(
            urllib.request.Request(base + path, headers=headers), timeout=60
        ) as response:
            return json.load(response)

    run = api(f"/actions/runs/{RUN}")
    if run["head_sha"] != RUN_SHA:
        raise ValueError("Input run head mismatch")
    INPUT.mkdir(parents=True, exist_ok=True)
    for name, (identifier, expected) in ARTIFACTS.items():
        metadata = api(f"/actions/artifacts/{identifier}")
        if metadata["expired"] or metadata["workflow_run"]["id"] != RUN:
            raise ValueError("Artifact provenance/expiry mismatch")
        if metadata.get("digest") != "sha256:" + expected:
            raise ValueError("Artifact API digest mismatch")
        request = urllib.request.Request(
            base + f"/actions/artifacts/{identifier}/zip", headers=headers
        )
        try:
            urllib.request.build_opener(ci.NoRedirect).open(request, timeout=60)
            raise ValueError("Expected artifact redirect")
        except urllib.error.HTTPError as error:
            if error.code != 302:
                raise
            signed = error.headers["Location"]
        # Credentials are never sent to the signed storage host or logged.
        archive = INPUT / (name + ".zip")
        with urllib.request.urlopen(signed, timeout=60) as response, archive.open(
            "wb"
        ) as out:
            shutil.copyfileobj(response, out)
        if ci.digest(archive) != expected:
            raise ValueError("Downloaded artifact digest mismatch")
        destination = INPUT / name
        destination.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            if sum(item.file_size for item in zipped.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Oversized archive")
            for item in zipped.infolist():
                path = Path(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsafe artifact path")
            zipped.extractall(destination)
        archive.unlink()
    previous = json.loads((INPUT / "reports/sources.json").read_text())
    for key, expected in [
        ("kernel_sha", ci.KERNEL_SHA),
        ("modules_sha", ci.MODULES_SHA),
        ("manager_sha", ci.MANAGER_SHA),
        ("susfs_sha", SUSFS_SHA),
    ]:
        if previous[key] != expected:
            raise ValueError("Unexpected recorded source " + key)
    for name, expected in TABLES.items():
        if ci.digest(INPUT / "reports" / name) != expected:
            raise ValueError("Authentic table digest mismatch")
    if ci.digest(INPUT / "baseline/camera.ko") != BASELINE_SHA:
        raise ValueError("Baseline digest mismatch")
    write(
        "input-provenance.json",
        {
            "run": RUN,
            "run_sha": RUN_SHA,
            "artifacts": ARTIFACTS,
            "tables": TABLES,
            "exploratory": True,
            "installation_approved": False,
        },
    )


def prepare():
    source = ci.read("sources.json")
    previous = json.loads((INPUT / "reports/sources.json").read_text())
    for key in [
        "kernel_sha",
        "modules_sha",
        "manager_sha",
        "susfs_sha",
        "susfs_patch_sha256",
    ]:
        if source[key] != previous[key]:
            raise ValueError("Probe environment does not reproduce " + key)
    if source["susfs_sha"] != SUSFS_SHA:
        raise ValueError("Moving SUSFS ref prohibited for this probe")
    ci.run(
        ci.make_args() + ["modules_prepare"],
        "probe-header-prepare",
        build_env("llvm-22.1.8"),
    )
    # modules_prepare intentionally does not create Module.symvers. Reuse the
    # unmodified authentic table from the earlier actual kernel build.
    shutil.copyfile(INPUT / "reports/kernel-Module.symvers", ci.KOUT / "Module.symvers")
    mmrm = ci.MODULES / "qcom/opensource/mmrm-driver"
    shutil.copyfile(INPUT / "reports/mmrm-Module.symvers", mmrm / "Module.symvers")
    for name in [
        "include/generated/autoconf.h",
        "include/generated/utsrelease.h",
        "include/generated/bounds.h",
        "include/generated/asm-offsets.h",
        "scripts/mod/modpost",
    ]:
        if not (ci.KOUT / name).is_file():
            raise ValueError("Prepared artifact missing: " + name)
    if ci.config_values(ci.KOUT / ".config") != ci.config_values(
        INPUT / "reports/rebuilt-config.txt"
    ):
        raise ValueError("Prepared configuration differs from original run")
    write(
        "prepared-environment.json",
        {
            "genuine_header_preparation": True,
            "kernel_recompiled": False,
            "mmrm_recompiled": False,
            "tables_reused_without_changes": TABLES,
            "configuration_sha256": ci.digest(ci.KOUT / ".config"),
        },
    )


def keep(module, label):
    folder = PROBE / label
    folder.mkdir(exist_ok=True)
    shutil.copyfile(module, folder / "camera.ko")
    for name in ["camera.lds", ".camera.ko.cmd", ".camera.lto.o.cmd"]:
        product = module.parent / name
        if product.exists():
            shutil.copyfile(product, folder / name.lstrip("."))
    (folder / "CLASSIFICATION.txt").write_text(
        "EXPLORATORY CODEGEN DIAGNOSTIC ONLY. NOT AN INSTALLATION CANDIDATE.\n"
    )
    reference = json.loads((ci.ROOT / "original-abi.json").read_text())
    reference_audit = json.loads((ci.ROOT / "original-audit.json").read_text())
    report = compare(module, reference, reference_audit)
    report.update(gate_passed=False, exploratory=True, installation_approved=False)
    (folder / "compatibility.json").write_text(json.dumps(report, indent=2) + "\n")
    return folder


def control():
    if ci.git(ci.MODULES, "diff", "--name-only", "HEAD"):
        raise ValueError("Control camera source changed")
    module = ci.camera(ci.MODULES, "probe-thin-control")
    folder = keep(module, "thin-control")
    observed, previous = inventory(module), inventory(INPUT / "baseline/camera.ko")
    fields = [
        "elf_type",
        "machine",
        "compiler",
        "modinfo",
        "imports_crc",
        "undefined",
        "exports",
        "absolute_export_crc",
        "module_struct_size",
    ]
    checks = {key: observed[key] == previous[key] for key in fields}
    checks["allocated_sections"] = {
        k: v for k, v in observed["sections"].items() if v["flags"] & 2
    } == {k: v for k, v in previous["sections"].items() if v["flags"] & 2}
    left, right = audit(module), audit(INPUT / "baseline/camera.ko")
    checks["strict_function_fingerprints"] = (
        left["function_fingerprints"] == right["function_fingerprints"]
    )
    checks["runtime_relocations"] = (
        left["runtime_relocation_type_counts"]
        == right["runtime_relocation_type_counts"]
    )
    # Whole-file hashes can differ due debug paths; compare pointer structures directly.
    l, r = pointer_inventory(module), pointer_inventory(INPUT / "baseline/camera.ko")
    checks["published_pointer_inventory"] = all(
        l[k] == r[k]
        for k in [
            "cfi_entry_count",
            "published_pointer_count",
            "publications",
            "invalid_published_pointers",
        ]
    )
    write(
        "control.json",
        {
            "checks": checks,
            "environment_control_reproduced": all(checks.values()),
            "release_gate_passed": False,
        },
    )
    if not all(checks.values()):
        raise ValueError(
            "Thin control differs: cannot attribute FullLTO results to LTO alone"
        )
    objects = {
        str(p.relative_to(ci.MODULES)): ci.digest(p)
        for p in (ci.MODULES / "qcom/opensource/camera-kernel").rglob("*.o")
    }
    write("control-object-checksums.json", objects)


def fix_relink():
    helper = ci.KERNEL / "scripts/generate_cfi_kallsyms.pl"
    if (
        ci.digest(helper)
        != "a4a8eb23cdbca209fb8846d153e7cc03a5d438cd239c0cf7852f2206de25164e"
    ):
        raise ValueError("Unexpected helper revision")
    ci.run(
        ["git", "-C", ci.KERNEL, "apply", "--check", ci.ROOT / "cfi-bti-parser.patch"],
        "probe-helper-patch",
    )
    ci.run(
        ["git", "-C", ci.KERNEL, "apply", ci.ROOT / "cfi-bti-parser.patch"],
        "probe-helper-patch",
    )
    camera = ci.MODULES / "qcom/opensource/camera-kernel"
    # Only discard generated final-link products. The saved control and all
    # compiled source objects/prelink .lto.o remain available for comparison.
    for name in ["camera.ko", ".camera.ko.cmd", "camera.lds"]:
        (camera / name).unlink(missing_ok=True)
    module = ci.camera(ci.MODULES, "probe-fixed-relink")
    folder = keep(module, "thin-fixed-relink")
    before = json.loads((PROBE / "control-object-checksums.json").read_text())
    after = {str(p.relative_to(ci.MODULES)): ci.digest(p) for p in camera.rglob("*.o")}
    checks = {
        "all_compiled_objects_unchanged": before == after,
        "published_targets_valid": not pointer_inventory(module)[
            "invalid_published_pointers"
        ],
    }
    checks["published_fields_match_rom"] = (
        pointer_inventory(module)["publications"]
        == json.loads((ci.ROOT / "original-audit.json").read_text())[
            "cfi_publications"
        ]["publications"]
    )
    write(
        "fixed-relink.json",
        {
            "checks": checks,
            "helper_patch_sha256": ci.digest(ci.ROOT / "cfi-bti-parser.patch"),
            "kernel_configuration_changed": False,
            "release_gate_passed": False,
        },
    )
    if not all(checks.values()):
        raise ValueError(
            "Helper fix did not isolate the expected publication correction"
        )


def full():
    flags = full_lto_flags(ci.config_values(ci.KOUT / ".config"))
    camera = ci.MODULES / "qcom/opensource/camera-kernel"
    ci.run(
        ci.make_args(True)
        + [
            "M=" + str(camera),
            "CAMERA_KERNEL_ROOT=" + str(camera),
            "KERNEL_ROOT=" + str(ci.KERNEL),
            "MODNAME=camera",
            "clean",
        ],
        "probe-full-clean",
        build_env("android-r563880c"),
    )
    before = ci.digest(ci.KOUT / ".config")
    args = ci.make_args(True) + [
        "M=" + str(camera),
        "CAMERA_KERNEL_ROOT=" + str(camera),
        "KERNEL_ROOT=" + str(ci.KERNEL),
        "MODNAME=camera",
        "TARGET_PRODUCT=marble",
        "KBUILD_EXTRA_SYMBOLS="
        + str(ci.MODULES / "qcom/opensource/mmrm-driver/Module.symvers"),
        "CC_FLAGS_LTO=" + flags,
        "V=1",
        "modules",
    ]
    ci.run(args, "probe-full-build", build_env("android-r563880c"))
    if ci.digest(ci.KOUT / ".config") != before:
        raise ValueError("Full module experiment changed boot configuration")
    if ci.git(ci.MODULES, "diff", "--name-only", "HEAD"):
        raise ValueError("Camera source changed")
    keep(camera / "camera.ko", "full-diagnostic")
    write(
        "full-flags.json",
        {
            "boot_kernel_configuration_lto": "ThinLTO",
            "experimental_camera_lto": "FullLTO",
            "explicit_CC_FLAGS_LTO": flags,
            "cfi_failure_mode": "native trap (CONFIG_UBSAN_TRAP=y)",
            "kernel_config_sha256": before,
            "source_changed": False,
            "helper_patch_sha256": ci.digest(ci.ROOT / "cfi-bti-parser.patch"),
            "installation_approved": False,
            "release_gate_passed": False,
        },
    )


def finalize():
    PROBE.mkdir(parents=True, exist_ok=True)
    for path in [
        ci.REPORTS / "sources.json",
        ci.REPORTS / "configuration.json",
        ci.REPORTS / "toolchains.json",
    ]:
        if path.exists():
            shutil.copyfile(path, PROBE / path.name)
    for label in ["thin-control", "thin-fixed-relink", "full-diagnostic"]:
        module = PROBE / label / "camera.ko"
        if module.exists():
            ci.run(
                [
                    "python3",
                    ci.ROOT / "deep_compare.py",
                    INPUT / "baseline/camera.ko",
                    module,
                    "--reference-kind",
                    "prior-baseline",
                    "--output",
                    PROBE / label / "versus-prior-baseline.json",
                ],
                "probe-" + label + "-accounting",
            )
    sums = "".join(
        ci.digest(p) + "  " + str(p.relative_to(PROBE)) + "\n"
        for p in sorted(PROBE.rglob("*"))
        if p.is_file() and p.name != "SHA256SUMS"
    )
    (PROBE / "SHA256SUMS").write_text(sums)
    (PROBE / "STATUS.txt").write_text(
        "DIAGNOSTIC EXPERIMENT ONLY. NO RELEASE GATE OPENED. NO INSTALLATION APPROVAL.\n"
    )


STAGES = {
    "inputs": inputs,
    "prepare": prepare,
    "control": control,
    "fix-relink": fix_relink,
    "full": full,
    "finalize": finalize,
}
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "stage": args.stage,
                    "kernel_build": False,
                    "mmrm_build": False,
                    "substantial_camera_builds": 2,
                    "source_run": RUN,
                    "gate_can_open": False,
                    "network_or_compilation": False,
                }
            )
        )
        raise SystemExit()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Probe stages may execute only on GitHub Actions")
    STAGES[args.stage]()
