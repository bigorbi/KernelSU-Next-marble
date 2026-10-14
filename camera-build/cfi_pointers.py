"""Read-only audit of function pointers actually published into allocated data.

A typed table-entry inventory does not validate pointers stored in operations
structures. No symbol addresses or relocations are rewritten by this tool.
"""

import bisect
import collections
import hashlib
import re
import struct
from elf_probe import Elf


def internal_name(symbol):
    name = symbol["name"]
    if symbol["section"] and (symbol["binding"] == 0 or symbol["visibility"] == 2):
        name = re.sub(r"\.[0-9a-f]{32}(?=\.|$)", "", name)
        name = re.sub(r"\.\d+$", "", name)
    return name


def pointer_inventory(path):
    e = Elf(path, allocated_only=True)
    text = e.names.index(".text")
    offset = e.ss[text][4]
    entries = {}
    symbols_at = collections.defaultdict(list)
    objects = collections.defaultdict(list)
    for symbol in e.sy:
        if symbol["type"] == 2 and symbol["size"] and symbol["section"]:
            symbols_at[symbol["section"], symbol["value"]].append(symbol)
        if symbol["type"] == 1 and symbol["size"] and 0 < symbol["section"] < len(e.ss):
            objects[symbol["section"]].append(symbol)
    for rows in objects.values():
        rows.sort(key=lambda s: s["value"])
    starts = {i: [s["value"] for s in rows] for i, rows in objects.items()}
    for address, kind, target, addend in e.rel[text]:
        if kind != 282 or e.d[offset + address - 4 : offset + address] != bytes.fromhex(
            "5f2403d5"
        ):
            continue
        if struct.unpack_from("<I", e.d, offset + address)[0] >> 26 != 5:
            raise ValueError("CFI entry relocation is not on a B instruction")
        if target["type"] == 3:
            names = [
                internal_name(s)
                for s in symbols_at[target["section"], target["value"] + addend]
                if ".cfi_jt" not in s["name"] and not s["name"].startswith("__typeid_")
            ]
        else:
            if addend:
                raise ValueError("Unexpected branch addend")
            names = [internal_name(target)]
        if not names:
            raise ValueError("Cannot identify CFI branch destination")
        entries[address - 4] = sorted(set(names))
    if not entries:
        raise ValueError("BTI CFI table not found")
    low, high = e.byname["__cfi_jt_start"]["value"], e.byname["__cfi_jt_end"]["value"]
    if not 0 <= low < high <= e.ss[text][5] or (high - low) % 8:
        raise ValueError("Invalid CFI table boundaries")
    if set(entries) != set(range(low, high, 8)):
        raise ValueError(
            "CFI table does not consist entirely of identified BTI/B entries"
        )
    published = {}
    invalid = []
    unattributed = []
    for index, rows in e.rel.items():
        if e.ss[index][2] & 4:
            continue
        for location, kind, target, addend in rows:
            if kind != 257 or target["section"] != text:
                continue
            address = target["value"] + addend
            if not low <= address < high:
                continue
            at = bisect.bisect_right(starts.get(index, []), location) - 1
            owner = objects.get(index, [])[at] if at >= 0 else None
            if (
                owner
                and owner["value"] <= location
                and location + 8 <= owner["value"] + owner["size"]
            ):
                field = (
                    f"{e.names[index]}:{internal_name(owner)}+{location-owner['value']}"
                )
            else:
                field = f"{e.names[index]}:UNATTRIBUTED+{location}"
                unattributed.append(field)
            if field in published:
                raise ValueError("Duplicate publication field")
            valid = address in entries
            published[field] = {
                "target_names": entries.get(address, []),
                "valid_bti_entry": valid,
            }
            if not valid:
                invalid.append(
                    {
                        "field": field,
                        "symbol": target["name"],
                        "symbol_addend": addend,
                        "text_offset": address,
                        "offset_within_entry": (address - low) % 8,
                        "reason": "Published pointer does not point to BTI/CFI entry start",
                    }
                )
    return {
        "module_sha256": hashlib.sha256(e.d).hexdigest(),
        "cfi_entry_count": len(entries),
        "published_pointer_count": len(published),
        "publications": dict(sorted(published.items())),
        "invalid_published_pointers": invalid,
        "unattributed_fields": unattributed,
    }
