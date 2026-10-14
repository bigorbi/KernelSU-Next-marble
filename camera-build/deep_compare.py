#!/usr/bin/env python3
"""Diagnostic ARM64 code/relocation accounting. Never supplies a compatibility gate."""
import argparse
import bisect
import collections
import hashlib
import json
import re
import struct
from pathlib import Path
from elf_probe import Elf

MASKS = {
    275: 0x60FFFFE0,
    277: 0x003FFC00,
    278: 0x003FFC00,
    285: 0x003FFC00,
    286: 0x003FFC00,
    282: 0x03FFFFFF,
    283: 0x03FFFFFF,
}
ORIGINAL_SHA = "a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1"


def canonical(name):
    # Diagnostic only. Duplicate names are retained as lists, never paired arbitrarily.
    name = re.sub(r"\.llvm\.\d+(?=\.|$)", "", name)
    name = re.sub(r"\.[0-9a-f]{32}(?=\.|$)", "", name)
    return re.sub(r"\.\d+$", "", name)


def union_size(ranges):
    total = end = 0
    for low, high in sorted(ranges):
        if low < 0 or high < low:
            raise ValueError("Invalid range")
        total += max(0, high - max(low, end))
        end = max(end, high)
    return total


def mask_instruction(word, kind):
    # Unlike v1, preserve opcodes and register operands. Unsupported kinds fail.
    return word & ~MASKS[kind]


class Image:
    def __init__(self, path):
        self.elf = Elf(path, allocated_only=True)
        self.rel = {
            i: sorted(rows, key=lambda r: r[0]) for i, rows in self.elf.rel.items()
        }
        self.addresses = {i: [r[0] for r in rows] for i, rows in self.rel.items()}
        self.by_section = collections.defaultdict(list)
        for symbol in self.elf.sy:
            if (
                0 < symbol["section"] < len(self.elf.ss)
                and symbol["name"]
                and symbol["size"]
            ):
                if symbol["type"] in (1, 2):
                    self.by_section[symbol["section"]].append(symbol)
        for rows in self.by_section.values():
            rows.sort(key=lambda s: s["value"])
        self.starts = {
            i: [s["value"] for s in rows] for i, rows in self.by_section.items()
        }
        self.functions = {}
        for symbol in self.elf.sy:
            index = symbol["section"]
            if (
                symbol["type"] != 2
                or not symbol["size"]
                or not 0 < index < len(self.elf.ss)
                or not self.elf.ss[index][2] & 4
                or ".cfi_jt" in symbol["name"]
                or symbol["name"].startswith("__typeid_")
            ):
                continue
            key = (index, symbol["value"], symbol["size"])
            self.functions.setdefault(key, []).append(symbol["name"])
        self.function_ranges = collections.defaultdict(list)
        for key, names in self.functions.items():
            index, start, size = key
            self.function_ranges[index].append(
                (start, start + size, tuple(sorted({canonical(n) for n in names})))
            )
        for rows in self.function_ranges.values():
            rows.sort()
        self.function_starts = {
            i: [r[0] for r in rows] for i, rows in self.function_ranges.items()
        }

    def rows(self, index, start, size):
        addresses = self.addresses.get(index, [])
        low = bisect.bisect_left(addresses, start)
        high = bisect.bisect_left(addresses, start + size)
        return self.rel.get(index, [])[low:high]

    def owner(self, index, address):
        rows = self.function_ranges.get(index, [])
        at = bisect.bisect_right(self.function_starts.get(index, []), address) - 1
        if at >= 0 and rows[at][0] <= address < rows[at][1]:
            return rows[at][2]
        return ("UNATTRIBUTED",)

    def target(self, symbol, addend):
        e = self.elf
        index = symbol["section"]
        if symbol["type"] != 3:
            # Undefined and public names are exact; local linker spelling is diagnostic.
            internal = symbol["section"] != 0 and (
                symbol["binding"] == 0 or symbol["visibility"] == 2
            )
            name = canonical(symbol["name"]) if internal else symbol["name"]
            return ("symbol", name, addend)
        address = symbol["value"] + addend
        rows = self.by_section.get(index, [])
        at = bisect.bisect_right(self.starts.get(index, []), address) - 1
        # Look through aliases starting at the same address; report all identities.
        selected = []
        if at >= 0:
            base = rows[at]["value"]
            while at >= 0 and rows[at]["value"] == base:
                row = rows[at]
                if row["value"] <= address < row["value"] + row["size"]:
                    if ".cfi_jt" not in row["name"] and not row["name"].startswith(
                        "__typeid_"
                    ):
                        selected.append(
                            (canonical(row["name"]), address - row["value"])
                        )
                at -= 1
        if selected:
            return ("section-symbols", e.names[index], sorted(set(selected)))
        section = e.ss[index]
        if section[2] & 32 and 0 <= address < section[5]:
            start = section[4] + address
            string = e.d[start : section[4] + section[5]].split(b"\0", 1)[0]
            return ("string-sha256", hashlib.sha256(string).hexdigest(), len(string))
        return ("UNRESOLVED-section-offset", e.names[index], address)

    def fingerprint(self, key):
        index, start, size = key
        e = self.elf
        section = e.ss[index]
        if start + size > section[5]:
            raise ValueError("Function outside section")
        body = bytearray(e.d[section[4] + start : section[4] + start + size])
        refs = []
        unresolved = 0
        relocated = set()
        for address, kind, target, addend in self.rows(index, start, size):
            offset = address - start
            if offset % 4 or offset + 4 > size:
                raise ValueError("Misaligned or truncated executable relocation")
            word = struct.unpack_from("<I", body, offset)[0]
            struct.pack_into("<I", body, offset, mask_instruction(word, kind))
            identity = self.target(target, addend)
            unresolved += identity[0].startswith("UNRESOLVED")
            refs.append((offset, kind, identity))
            relocated.add(offset)
        # A branch resolved by LLD can move with function ordering. Only normalize
        # external-to-this-body branches with an identifiable destination entry.
        for offset in range(0, size - size % 4, 4):
            if offset in relocated:
                continue
            word = struct.unpack_from("<I", body, offset)[0]
            if word >> 26 not in (5, 37):
                continue
            imm = word & 0x03FFFFFF
            if imm & (1 << 25):
                imm -= 1 << 26
            destination = start + offset + imm * 4
            if start <= destination < start + size:
                continue
            candidates = [
                r
                for r in self.by_section.get(index, [])
                if r["type"] == 2
                and r["value"] == destination
                and ".cfi_jt" not in r["name"]
                and not r["name"].startswith("__typeid_")
            ]
            if candidates:
                struct.pack_into("<I", body, offset, word & 0xFC000000)
                refs.append(
                    (
                        offset,
                        "linked-branch",
                        sorted({canonical(r["name"]) for r in candidates}),
                    )
                )
        payload = (
            bytes(body)
            + json.dumps(
                sorted(refs, key=lambda r: r[0]), separators=(",", ":")
            ).encode()
        )
        return {
            "size": size,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "unresolved_references": unresolved,
        }

    def function_inventory(self):
        result = collections.defaultdict(list)
        for key, names in self.functions.items():
            record = self.fingerprint(key)
            record["aliases"] = sorted({canonical(n) for n in names})
            for name in record["aliases"]:
                result[name].append(record)
        return dict(result)

    def section_accounting(self):
        e = self.elf
        output = {}
        for name in [".text", ".data", ".bss", ".rodata"]:
            index = e.names.index(name)
            section = e.ss[index]
            if name == ".text":
                bodies = [(lo, hi) for lo, hi, _ in self.function_ranges[index]]
                # Count actual BTI/B pairs, not labels. Some synthesized baseline
                # labels point at B (+4), which is itself a discovered defect.
                slots = [
                    (address - 4, address + 4)
                    for address, kind, target, addend in self.rel[index]
                    if kind == 282
                    and e.d[section[4] + address - 4 : section[4] + address]
                    == bytes.fromhex("5f2403d5")
                ]
                body_size = union_size(bodies)
                joint = union_size(bodies + slots)
                output[name] = {
                    "size": section[5],
                    "function_body_union": body_size,
                    "jump_entry_union_outside_bodies": joint - body_size,
                    "unattributed_or_padding": section[5] - joint,
                }
            else:
                ranges = [
                    (s["value"], s["value"] + s["size"])
                    for s in self.by_section[index]
                    if s["type"] == 1
                ]
                size = union_size(ranges)
                output[name] = {
                    "size": section[5],
                    "object_body_union": size,
                    "unattributed_or_padding": section[5] - size,
                }
        return output

    def object_inventory(self):
        result = collections.defaultdict(list)
        for index, symbols in self.by_section.items():
            if self.elf.names[index] not in (".data", ".bss", ".rodata"):
                continue
            seen = set()
            for symbol in symbols:
                if symbol["type"] != 1:
                    continue
                key = (symbol["value"], symbol["size"])
                if key in seen:
                    continue
                seen.add(key)
                name = canonical(symbol["name"])
                result[self.elf.names[index] + ":" + name].append(symbol["size"])
        return dict(result)

    def jump_tables(self):
        e = self.elf
        index = e.names.index("__jump_table")
        section = e.ss[index]
        rel = {row[0]: row for row in self.rel[index]}
        keys = collections.Counter()
        by_function = collections.defaultdict(collections.Counter)
        for offset in range(0, section[5], 16):
            address, kind, symbol, addend = rel[offset + 8]
            if kind != 260:
                raise ValueError("Unexpected jump key relocation")
            key = str(self.target(symbol, addend))
            keys[key] += 1
            _, code_kind, code, code_addend = rel[offset]
            if code_kind != 261:
                raise ValueError("Unexpected jump code relocation")
            owner = self.owner(code["section"], code["value"] + code_addend)
            by_function["|".join(owner)][key] += 1
        return {
            "entry_count": section[5] // 16,
            "keys": dict(keys),
            "by_function": {name: dict(counts) for name, counts in by_function.items()},
        }

    def alternatives(self):
        e = self.elf
        index = e.names.index(".altinstructions")
        section = e.ss[index]
        rel = {row[0]: row for row in self.rel[index]}
        counts = collections.Counter()
        owners = collections.defaultdict(collections.Counter)
        for offset in range(0, section[5], 12):
            feature, original, replacement = struct.unpack_from(
                "<HBB", e.d, section[4] + offset + 8
            )
            label = f"{feature}:{original}:{replacement}"
            counts[label] += 1
            _, kind, symbol, addend = rel[offset]
            if kind != 261:
                raise ValueError("Unexpected alternative code relocation")
            owner = self.owner(symbol["section"], symbol["value"] + addend)
            owners["|".join(owner)][label] += 1
        return {
            "entry_count": section[5] // 12,
            "feature_length_counts": dict(counts),
            "by_function": {name: dict(rows) for name, rows in owners.items()},
        }

    def calls(self):
        totals = collections.Counter()
        owners = collections.defaultdict(collections.Counter)
        for index, rows in self.rel.items():
            if not self.elf.ss[index][2] & 4:
                continue
            for address, kind, symbol, addend in rows:
                if kind != 283:
                    continue
                target = str(self.target(symbol, addend))
                totals[target] += 1
                owners["|".join(self.owner(index, address))][target] += 1
        return {
            "targets": dict(totals),
            "by_function": {name: dict(rows) for name, rows in owners.items()},
        }


def compare(original, baseline):
    left, right = Image(original), Image(baseline)
    a, b = left.function_inventory(), right.function_inventory()
    changes = {}
    equal = []
    ambiguous = []
    for name in sorted(a.keys() & b.keys()):
        if len(a[name]) != 1 or len(b[name]) != 1:
            ambiguous.append(name)
            continue
        x, y = a[name][0], b[name][0]
        if (
            x["sha256"] == y["sha256"]
            and not x["unresolved_references"]
            and not y["unresolved_references"]
        ):
            equal.append(name)
        else:
            changes[name] = {
                "original": x,
                "baseline": y,
                "size_delta": y["size"] - x["size"],
            }
    return {
        "method": "Diagnostic opcode-preserving immediate masking and resolved symbol/string identities; not C source proof or a release gate.",
        "input_sha256": {
            "original": hashlib.sha256(left.elf.d).hexdigest(),
            "baseline": hashlib.sha256(right.elf.d).hexdigest(),
        },
        "section_accounting": {
            "original": left.section_accounting(),
            "baseline": right.section_accounting(),
        },
        "function_comparison": {
            "original_names": len(a),
            "baseline_names": len(b),
            "normalized_exact_matches_without_unresolved_refs": equal,
            "ambiguous_names_not_paired": ambiguous,
            "only_original": sorted(a.keys() - b.keys()),
            "only_baseline": sorted(b.keys() - a.keys()),
            "changed": changes,
        },
        "jump_tables": {
            "original": left.jump_tables(),
            "baseline": right.jump_tables(),
        },
        "alternatives": {
            "original": left.alternatives(),
            "baseline": right.alternatives(),
        },
        "calls": {"original": left.calls(), "baseline": right.calls()},
        "object_sizes": {
            "original": left.object_inventory(),
            "baseline": right.object_inventory(),
        },
        "compatibility_gate_unchanged": True,
        "installation_approved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original")
    parser.add_argument("baseline")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--reference-kind", choices=["rom", "prior-baseline"], default="rom"
    )
    args = parser.parse_args()
    expected = (
        ORIGINAL_SHA
        if args.reference_kind == "rom"
        else "5d8e4c2e9a9b72c058cfae0473f561734bad3382550ea130c33514f3efd85d2c"
    )
    if hashlib.sha256(Path(args.original).read_bytes()).hexdigest() != expected:
        raise ValueError(
            "Reference SHA differs from authenticated " + args.reference_kind + " input"
        )
    result = compare(args.original, args.baseline)
    result["reference_kind"] = args.reference_kind
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    functions = result["function_comparison"]
    print(
        "Exact resolved diagnostic matches:",
        len(functions["normalized_exact_matches_without_unresolved_refs"]),
    )
    print(
        "Changed:",
        len(functions["changed"]),
        "ambiguous:",
        len(functions["ambiguous_names_not_paired"]),
    )
    print(json.dumps(result["section_accounting"], indent=2))


if __name__ == "__main__":
    main()
