"""Independent ARM64 execution of actual __cfi_check. Never loads a module."""

import sys, pathlib, struct, hashlib, json, collections, re
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0,
    UC_ARM64_REG_X1,
    UC_ARM64_REG_X2,
    UC_ARM64_REG_X30,
    UC_ARM64_REG_SP,
)
from elf_probe import Elf

BASE = 0x1000000
EXIT = 0x2000000


def emulate(path):
    e = Elf(path)
    s = e.byname["__cfi_check"]
    si = s["section"]
    sec = e.ss[si]
    text = bytearray(e.d[sec[4] : sec[4] + sec[5]])
    for a, k, t, v in e.rel[si]:
        if not s["value"] <= a < s["value"] + s["size"]:
            continue
        if t["section"] != si:
            raise ValueError("Dispatcher refers outside .text")
        value = BASE + t["value"] + v
        word = struct.unpack_from("<I", text, a)[0]
        if k == 275:
            imm = ((value >> 12) - ((BASE + a) >> 12)) & 0x1FFFFF
            word = (word & ~0x60FFFFE0) | ((imm & 3) << 29) | ((imm >> 2) << 5)
        elif k == 277:
            word = (word & ~0x003FFC00) | ((value & 0xFFF) << 10)
        elif k == 283:
            word = (word & 0xFC000000) | (((value - (BASE + a)) >> 2) & 0x03FFFFFF)
        else:
            raise ValueError("Unexpected dispatcher relocation " + str(k))
        struct.pack_into("<I", text, a, word)
    u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    u.mem_map(BASE, (len(text) + 4095) & ~4095)
    u.mem_write(BASE, bytes(text))
    u.mem_map(EXIT, 4096)
    u.mem_map(0x3000000, 65536)
    u.reg_write(UC_ARM64_REG_SP, 0x3008000)
    fail = BASE + e.byname["__cfi_check_fail"]["value"]
    state = {}

    def hook(uc, pc, size, unused):
        if pc in [EXIT, fail]:
            state["accepted"] = pc == EXIT
            uc.emu_stop()

    u.hook_add(UC_HOOK_CODE, hook)

    def accepts(tid, address):
        state.clear()
        u.reg_write(UC_ARM64_REG_X0, tid)
        u.reg_write(UC_ARM64_REG_X1, BASE + address)
        u.reg_write(UC_ARM64_REG_X2, 0)
        u.reg_write(UC_ARM64_REG_X30, EXIT)
        u.reg_write(UC_ARM64_REG_SP, 0x3008000)
        u.emu_start(BASE + s["value"], EXIT + 4, count=1000)
        if "accepted" not in state:
            raise ValueError("Unexpected dispatch termination")
        return state["accepted"]

    # Enumerate constants from the actual dispatcher, including optimizer pivot +/-1.
    values = []
    registers = {}
    body = e.body("__cfi_check")
    for off in range(0, len(body), 4):
        w = struct.unpack_from("<I", body, off)[0]
        rd = w & 31
        hw = (w >> 21) & 3
        imm = (w >> 5) & 65535
        op = w & 0xFF800000
        if op == 0xD2800000:
            registers[rd] = imm << (hw * 16)
        elif op == 0x92800000:
            registers[rd] = (~(imm << (hw * 16))) & ((1 << 64) - 1)
        elif op == 0xF2800000 and rd in registers:
            registers[rd] = (registers[rd] & ~(65535 << (hw * 16))) | (imm << (hw * 16))
        elif w == 0xEB08001F:
            values.append(registers[8])
    candidates = sorted({(v + d) & ((1 << 64) - 1) for v in values for d in [-1, 0, 1]})
    bases = sorted(
        {
            t["value"] + v
            for a, k, t, v in e.rel[si]
            if s["value"] <= a < s["value"] + s["size"] and k == 275
        }
    )
    ids = [tid for tid in candidates if any(accepts(tid, a) for a in bases)]
    addresses = {
        x["value"] for x in e.sy if ".cfi_jt" in x["name"] and x["section"] == si
    }
    low = min(addresses)
    high = max(addresses) + 8
    addresses = set(range(low, high, 8))
    result = {}
    tests = 0
    for tid in ids:
        accepted = [a for a in sorted(addresses) if accepts(tid, a)]
        tests += len(addresses)
        result[f"{tid:016x}"] = {"type_id": f"{tid:016x}", "addresses": accepted}
    print(
        path,
        "type IDs",
        len(result),
        "accepted slots",
        sum(len(v["addresses"]) for v in result.values()),
        "tests",
        tests,
        flush=True,
    )
    return e, result, accepts


def norm(name):
    return re.sub(
        r"\.[0-9a-f]{32}(?=\.|$)", "", re.sub(r"\.\d+(?=\.cfi_jt$|$)", "", name)
    )


def targets(e, groups):
    sy = e.sy
    sec = e.byname["__cfi_check"]["section"]
    start = e.ss[sec][4]
    rels = {a: (k, t, v) for a, k, t, v in e.rel[sec]}
    result = {}
    aliases = collections.defaultdict(list)
    for s in sy:
        if (
            s["section"] == sec
            and s["type"] == 2
            and s["name"]
            and not s["name"].startswith("__typeid_")
        ):
            aliases[s["value"]].append(s["name"])
    for name, group in groups.items():
        entries = []
        for a in group["addresses"]:
            if e.d[start + a : start + a + 4] != bytes.fromhex("5f2403d5"):
                raise ValueError("Expected BTI c jump entry")
            word = struct.unpack_from("<I", e.d, start + a + 4)[0]
            if word >> 26 != 5:
                raise ValueError("Expected B instruction")
            if a + 4 in rels:
                k, t, v = rels[a + 4]
                if k != 282:
                    raise ValueError("Unexpected jump relocation")

                if t["section"] == sec:
                    names = aliases[t["value"] + v]
                else:
                    if v:
                        raise ValueError("External jump with nonzero addend")
                    names = [t["name"]]
            else:
                imm = word & 0x3FFFFFF
                imm = imm - (1 << 26) if imm & (1 << 25) else imm
                target = a + 4 + imm * 4
                names = aliases[target]
            if not names:
                raise ValueError("Jump has no named target")
            entries.append(sorted(set(norm(n) for n in names)))
        result[name] = {"type_id": group["type_id"], "targets": sorted(entries)}
    return result


if __name__ == "__main__":
    import argparse, unicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original")
    parser.add_argument("baseline")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if (
        hashlib.sha256(pathlib.Path(args.original).read_bytes()).hexdigest()
        != "a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1"
    ):
        raise ValueError(
            "Original module SHA does not match the official ROM reference"
        )
    e, a, ac = emulate(args.original)
    f, b, bc = emulate(args.baseline)
    x = targets(e, a)
    y = targets(f, b)
    changes = {
        n: {"original": x.get(n), "baseline": y.get(n)}
        for n in x.keys() | y.keys()
        if x.get(n) != y.get(n)
    }
    negatives = {}
    for label, groups, accepts in [("original", a, ac), ("baseline", b, bc)]:
        ids = [int(n, 16) for n in groups]
        cases = 0
        for i, n in enumerate(groups):
            address = groups[n]["addresses"][0]
            tid = int(n, 16)
            assert accepts(tid, address)
            assert not accepts(tid, address + 1)
            assert not accepts(tid, address - 1)
            assert not accepts(ids[(i + 1) % len(ids)], address)
            cases += 3
        negatives[label] = {"rejected_wrong_type_and_unaligned_pointers": cases}
    # Change a jump relocation to the destination of a different typed entry.
    # The table and dispatcher stay intact; target decoding must catch it.
    import tempfile

    sec = f.byname["__cfi_check"]["section"]
    slots = [v for group in b.values() for v in group["addresses"]]
    first, second = slots[0], slots[-1]
    relocation_offsets = {}
    for section in f.ss:
        if section[1] == 4 and section[7] == sec:
            for offset in range(section[4], section[4] + section[5], 24):
                address, info, addend = struct.unpack_from("<QQq", f.d, offset)
                if info & 0xFFFFFFFF == 282:
                    relocation_offsets[address] = offset
    first_offset = relocation_offsets[first + 4]
    second_offset = relocation_offsets[second + 4]
    altered = bytearray(f.d)
    altered[first_offset + 8 : first_offset + 24] = f.d[
        second_offset + 8 : second_offset + 24
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "mutated.ko"
        path.write_bytes(altered)
        mutated = targets(Elf(path), b)
        assert mutated != y, "Redirected CFI destination was not detected"
    out = {
        "method": "Unicorn execution of relocated ARM64 dispatcher. IDs recovered from MOVZ/MOVN/MOVK/CMP constants and pivots; every eight-byte table slot tested. BTI/B destinations decoded. Specific to this dispatcher form.",
        "unicorn_version": unicorn.__version__,
        "input_sha256": {
            "original": hashlib.sha256(e.d).hexdigest(),
            "baseline": hashlib.sha256(f.d).hexdigest(),
        },
        "original": x,
        "baseline": y,
        "changes": changes,
        "equal_diagnostic_type_target_sets": not changes,
        "negative_tests": negatives,
        "redirected_destination_mutation_detected": True,
        "limitation": "Internal 32-hex and numeric suffixes normalized only for this diagnostic. Named destination equality does not prove destination body equivalence, full CFI equivalence, or historical source identity. No release gate is changed.",
    }
    pathlib.Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    print(
        "Mismatched groups",
        len(changes),
        "negative tests",
        negatives,
        "destination mutation detected",
    )
