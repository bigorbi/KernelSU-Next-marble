#!/usr/bin/env python3
"""Read-only accounting for retained run 38626370150; never supplies a gate."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import struct

from deep_compare import canonical
from elf_probe import Elf

ORIGINAL = "a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1"
MODULES = {
    "thin-control": "5d8e4c2e9a9b72c058cfae0473f561734bad3382550ea130c33514f3efd85d2c",
    "thin-fixed-relink": "da19b24f551e3152374490013655013d674a120155e86f92e49eba200d37ba48",
    "full-diagnostic": "f357af1129965f3fed2d6916e21e806ee19547f9612041904e6faedb4ff93864",
}
ARCHIVES = {
    "camera-codegen-experiment.zip": "ff3df086454a4c09fc156c677ecf11439caa48bd5f995c1583d4fd7a5752445d",
    "kernel-build-logs.zip": "7e187f81cc6d5bf4ece2b706f68bb204a5b0dbe65245154ec6342dc72c5e193d",
}
HANDLER = "__ubsan_handle_cfi_check_fail_abort"


def read(path):
    return json.loads(path.read_text())


def verified(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Unexpected SHA-256: " + str(path))


def raw_functions(elf):
    groups = collections.defaultdict(dict)
    for symbol in elf.sy:
        if symbol["type"] == 2 and symbol["size"] and 0 < symbol["section"] < len(elf.ss):
            section = elf.ss[symbol["section"]]
            start = section[4] + symbol["value"]
            key = (symbol["section"], symbol["value"], symbol["size"])
            groups[canonical(symbol["name"])][key] = elf.d[start:start + symbol["size"]]
    return groups


def failure_summary(elf):
    body = elf.body("__cfi_check_fail")
    return {
        "size": len(body),
        "brk_instructions": sum(
            struct.unpack_from("<I", body, n)[0] & 0xFFE0001F == 0xD4200000
            for n in range(0, len(body), 4)
        ),
        "handler_calls": sum(
            kind == 283 and target["name"] == HANDLER
            for _, kind, target, _ in elf.refs("__cfi_check_fail")
        ),
    }


def analyze(root, original):
    for name, digest in ARCHIVES.items():
        verified(root / name, digest)
    verified(original, ORIGINAL)
    probe = root / "camera-codegen-experiment"
    for label, digest in MODULES.items():
        verified(probe / label / "camera.ko", digest)
    run = read(root / "run.json")
    if (run["id"], run["head_sha"], run["conclusion"], run["run_attempt"]) != (
        38626370150, "627a7f7e08e98c5c7460c612664e6e4c2093fbce", "success", 1
    ):
        raise ValueError("Unexpected run provenance")
    full = read(root / "full-versus-rom.json")
    thin = read(root / "thin-fixed-versus-rom.json")
    for label, result in [("full-diagnostic", full), ("thin-fixed-relink", thin)]:
        if result["input_sha256"] != {"original": ORIGINAL, "baseline": MODULES[label]}:
            raise ValueError("Detailed comparison input mismatch")
    compat = {label: read(probe / label / "compatibility.json") for label in MODULES}
    left = Elf(original, allocated_only=True)
    right = Elf(probe / "full-diagnostic/camera.ko", allocated_only=True)
    lrefs, rrefs = left.refs("__cfi_check"), right.refs("__cfi_check")
    shape = lambda rows: [(offset, kind, target["name"]) for offset, kind, target, _ in rows]
    changed_cfi = compat["full-diagnostic"]["mismatch_details"]["cfi_fingerprints"]["changed"]
    jumps = [name for name in changed_cfi if name.endswith(".cfi_jt")]
    lfuncs, rfuncs = raw_functions(left), raw_functions(right)
    changed = full["function_comparison"]["changed"]
    equal_raw = [name for name in changed if len(lfuncs[name]) == len(rfuncs[name]) == 1
                 and next(iter(lfuncs[name].values())) == next(iter(rfuncs[name].values()))]
    different_raw = [name for name in changed if len(lfuncs[name]) == len(rfuncs[name]) == 1
                     and next(iter(lfuncs[name].values())) != next(iter(rfuncs[name].values()))]
    handler_key = f"('symbol', '{HANDLER}', 0)"
    full_calls = full["calls"]["baseline"]
    original_calls = full["calls"]["original"]
    without_handler = lambda calls: {k: v for k, v in calls.items() if k != handler_key}
    nonhandler_owners = {name: without_handler(calls) for name, calls in full_calls["by_function"].items()}
    nonhandler_owners = {name: calls for name, calls in nonhandler_owners.items() if calls}
    full_imports = compat["full-diagnostic"]["baseline_inventory"]["imports_crc"]
    original_imports = compat["thin-control"]["baseline_inventory"]["imports_crc"]
    return {
        "run": run["id"], "commit": run["head_sha"], "workflow_conclusion": run["conclusion"],
        "archive_sha256": ARCHIVES, "module_sha256": dict(original=ORIGINAL, **MODULES),
        "control": read(probe / "control.json"),
        "fixed_relink": read(probe / "fixed-relink.json"),
        "failed_checks": {label: c["failed_checks"] for label, c in compat.items()},
        "full_extra_imports": sorted(set(full_imports) - set(original_imports)),
        "all_original_import_crcs_preserved": all(full_imports.get(name) == crc for name, crc in original_imports.items()),
        "full_handler_call_count": full["calls"]["baseline"]["targets"].get(handler_key, 0),
        "full_handler_caller_count": sum(handler_key in calls for calls in full["calls"]["baseline"]["by_function"].values()),
        "nonhandler_call_histograms_equal": without_handler(full_calls["targets"]) == original_calls["targets"],
        "nonhandler_per_function_call_histograms_equal": nonhandler_owners == original_calls["by_function"],
        "failure_path": {"original": failure_summary(left), "full": failure_summary(right)},
        "cfi_dispatcher": {
            "raw_instruction_bytes_equal": left.body("__cfi_check") == right.body("__cfi_check"),
            "relocation_shape_and_names_equal": shape(lrefs) == shape(rrefs),
            "relocation_addend_delta_counts": dict(collections.Counter(
                b[3] - a[3] for a, b in zip(lrefs, rrefs))),
            "changed_cfi_jump_fingerprint_count": len(jumps),
            "changed_jump_raw_bytes_equal": all(left.body(n) == right.body(n) for n in jumps),
        },
        "runtime_relocation_mismatches": compat["full-diagnostic"]["mismatch_details"]["runtime_relocations"],
        "section_accounting": full["section_accounting"],
        "instrumentation": {key: {label: {k: v for k, v in values.items() if k != "by_function"}
                                  for label, values in full[key].items()}
                            for key in ["jump_tables", "alternatives"]},
        "function_comparison": {
            "thin_exact_resolved_pairs": len(thin["function_comparison"]["normalized_exact_matches_without_unresolved_refs"]),
            "full_exact_resolved_pairs": len(full["function_comparison"]["normalized_exact_matches_without_unresolved_refs"]),
            "full_unproved_pairs": len(changed),
            "full_unproved_pairs_with_equal_raw_instruction_bytes": len(equal_raw),
            "full_unproved_pairs_with_different_raw_instruction_bytes": len(different_raw),
            "raw_changed_pairs_without_handler_calls": [name for name in different_raw
                if handler_key not in full_calls["by_function"].get(name, {})],
            "full_ambiguous_names": len(full["function_comparison"]["ambiguous_names_not_paired"]),
            "full_only_original": full["function_comparison"]["only_original"],
            "full_only_baseline": full["function_comparison"]["only_baseline"],
        },
        "limitation": "Equal raw instructions do not establish relocated references or source equivalence. Diagnostic only; gates unchanged.",
        "installation_approved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.evidence, args.original)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Verified retained artifacts and reproduced diagnostic accounting; no gate changed.")


if __name__ == "__main__":
    main()
