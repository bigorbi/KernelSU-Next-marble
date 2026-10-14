#!/usr/bin/env python3
"""Non-binary ELF audit: relocations and normalized function/CFI fingerprints."""
import collections
import bisect
import hashlib
import json
import re
import struct
from pathlib import Path
from module_inventory import cstr, inventory


def normal(name):
    return re.sub(r'\.llvm\.\d+', '.llvm', name)


def cfi_symbol_surface(records):
    """Internal linker spelling only; retain multiplicity and every public/undefined name.

    This is a symbol surface check, never a substitute for the separately gated
    CFI dispatcher/target and compiled-code checks.
    """
    result = []
    for row in records:
        name = row['name']
        internal = row['defined'] and (row['binding'] == 0 or row['visibility'] == 2)
        if row['defined'] and row['type'] == 0 and row['size'] == 0 and name == '$x.cfi_jt':
            # This ROM's LTO-generated table anchor is GLOBAL NOTYPE, size zero.
            # It is absent in the baseline but its entire typed table is covered
            # independently. It is neither an exported function nor a target.
            continue
        if internal and row['type'] in (0, 2) and name.endswith('.cfi_jt'):
            name = re.sub(r'\.[0-9a-f]{32}(?=\.cfi_jt$)', '', name)
            name = re.sub(r'\.\d+(?=\.cfi_jt$)', '', name)
        result.append(name)
    return sorted(result)


def audit(path):
    data = Path(path).read_bytes()
    elf = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    assert data[:6] == b'\x7fELF\x02\x01' and elf[2] == 183
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, elf[6]+elf[11]*i) for i in range(elf[12])]
    names = sections[elf[13]]
    strings = data[names[4]:names[4]+names[5]]
    section_names = [cstr(strings, section[0]) for section in sections]
    table = sections[section_names.index('.symtab')]
    names = sections[table[6]]
    strings = data[names[4]:names[4]+names[5]]
    symbols = []
    for offset in range(table[4], table[4]+table[5], table[9]):
        name, info, other, section, value, size = struct.unpack_from('<IBBHQQ', data, offset)
        symbols.append({'name': cstr(strings, name), 'type': info & 15, 'section': section,
                        'value': value, 'size': size})
    relocations = collections.defaultdict(list)
    properties = {}
    runtime_properties = {}
    for section in sections:
        if section[1] != 4:
            continue
        counts = collections.Counter()
        for offset in range(section[4], section[4]+section[5], section[9]):
            address, info, addend = struct.unpack_from('<QQq', data, offset)
            target = symbols[info >> 32]
            kind = info & 0xffffffff
            counts[str(kind)] += 1
            relocations[section[7]].append((address, kind, normal(target['name']), addend))
        relocation_name = cstr(data[sections[elf[13]][4]:sections[elf[13]][4]+sections[elf[13]][5]], section[0])
        properties[relocation_name] = dict(counts)
        # sh_info identifies the relocation target. Section names alone are not
        # sufficient: even a section called .debug_info can be SHF_ALLOC.
        if sections[section[7]][2] & 2:
            runtime_properties[relocation_name] = dict(counts)
    fingerprints = collections.defaultdict(list)
    cfi = collections.defaultdict(list)
    for index in relocations:
        relocations[index].sort()
    relocation_addresses = {index: [row[0] for row in rows] for index, rows in relocations.items()}
    for symbol in symbols:
        index, size = symbol['section'], symbol['size']
        if symbol['type'] != 2 or not size or not (0 < index < len(sections)):
            continue
        section = sections[index]
        if not section[2] & 4:
            continue
        start = symbol['value']
        body = bytearray(data[section[4]+start:section[4]+start+size])
        if len(body) != size:
            raise ValueError('Function outside ELF section')
        refs = []
        rows = relocations[index]
        addresses = relocation_addresses.get(index, [])
        lower = bisect.bisect_left(addresses, start)
        upper = bisect.bisect_left(addresses, start+size)
        for address, kind, target, addend in rows[lower:upper]:
            if start <= address < start+size:
                relative = address-start
                # AArch64 instruction relocations are four bytes; mask their linked bits.
                body[relative:relative+min(4, size-relative)] = b'\0' * min(4, size-relative)
                refs.append((relative, kind, target, addend))
        digest = hashlib.sha256(body + json.dumps(sorted(refs), separators=(',', ':')).encode()).hexdigest()
        record = {'size': size, 'sha256': digest}
        fingerprints[normal(symbol['name'])].append(record)
        if 'cfi' in symbol['name'].lower():
            cfi[normal(symbol['name'])].append(record)
    ordered = lambda values: {name: sorted(rows, key=lambda row: (row['size'], row['sha256'])) for name, rows in sorted(values.items())}
    return {'module_sha256': hashlib.sha256(data).hexdigest(), 'machine': elf[2],
            'relocation_type_counts': properties, 'function_fingerprints': ordered(fingerprints),
            'runtime_relocation_type_counts': runtime_properties,
            'cfi_fingerprints': ordered(cfi),
            'method': 'Conservative masked-instruction/relocation fingerprint v1. Link-layout differences can fail; they never justify silently passing source equivalence.'}


def compare(baseline, reference, reference_audit):
    observed = inventory(baseline)
    actual_audit = audit(baseline)
    keys = ['elf_type', 'machine', 'compiler', 'modinfo', 'imports_crc', 'undefined',
            'exports', 'absolute_export_crc', 'module_struct_size', 'signature_trailer']
    checks = {key: observed[key] == reference[key] for key in keys}
    alloc = lambda value: {key: row for key, row in value['sections'].items() if row['flags'] & 2}
    checks['allocatable_sections'] = alloc(observed) == alloc(reference)
    checks['cfi_symbols'] = ('cfi_symbol_records' in reference and
                            cfi_symbol_surface(observed['cfi_symbol_records']) ==
                            cfi_symbol_surface(reference['cfi_symbol_records']))
    # Non-allocated DWARF relocations are discarded by strip-debug and are not
    # processed by the module loader. Keep them as diagnostics, not a runtime
    # compatibility condition. A missing reference inventory fails closed.
    checks['relocation_properties'] = (
        'runtime_relocation_type_counts' in reference_audit and
        actual_audit['runtime_relocation_type_counts'] == reference_audit['runtime_relocation_type_counts'])
    checks['cfi_type_dispatch_and_targets'] = bool(actual_audit['cfi_fingerprints'].get('__cfi_check')) and actual_audit['cfi_fingerprints'] == reference_audit['cfi_fingerprints']
    checks['compiled_source_equivalence'] = actual_audit['function_fingerprints'] == reference_audit['function_fingerprints']
    return {'checks': checks, 'necessary_abi_and_code_checks_pass': all(checks.values()),
            'mismatch_details': mismatch_details(observed, reference, actual_audit, reference_audit),
            'baseline_inventory': observed, 'baseline_audit': actual_audit,
            'failed_checks': [key for key, value in checks.items() if not value]}


def mismatch_details(observed, reference, actual, expected):
    """Explain strict failures without interpreting binary differences as proof of source changes."""
    def delta(left, right):
        return {name: {'baseline': left.get(name), 'original': right.get(name)}
                for name in sorted(left.keys() | right.keys()) if left.get(name) != right.get(name)}

    def fingerprints(key):
        left, right = actual[key], expected[key]
        common = left.keys() & right.keys()
        changed = sorted(name for name in common if left[name] != right[name])
        sizes = lambda rows: sorted(row['size'] for row in rows)
        return {'baseline_names': len(left), 'original_names': len(right),
                'exact_matches': sum(left[name] == right[name] for name in common),
                'only_baseline': sorted(left.keys() - right.keys()),
                'only_original': sorted(right.keys() - left.keys()),
                'changed': changed,
                'changed_sizes': {name: {'baseline': sizes(left[name]), 'original': sizes(right[name])}
                                  for name in changed if sizes(left[name]) != sizes(right[name])}}

    allocated = lambda value: {name: row for name, row in value['sections'].items() if row['flags'] & 2}
    left, right = set(map(normal, observed['cfi_symbols'])), set(map(normal, reference['cfi_symbols']))
    return {'allocatable_sections': delta(allocated(observed), allocated(reference)),
            'cfi_symbols': {'raw_only_baseline': sorted(left-right), 'raw_only_original': sorted(right-left),
                            'internal_symbol_surface_matches': ('cfi_symbol_records' in reference and
                                cfi_symbol_surface(observed['cfi_symbol_records']) ==
                                cfi_symbol_surface(reference['cfi_symbol_records']))},
            'runtime_relocations': delta(actual['runtime_relocation_type_counts'], expected.get('runtime_relocation_type_counts', {})),
            'all_relocations_including_non_allocated': delta(actual['relocation_type_counts'], expected['relocation_type_counts']),
            'cfi_fingerprints': fingerprints('cfi_fingerprints'),
            'function_fingerprints': fingerprints('function_fingerprints'),
            'interpretation': 'Exact v1 fingerprints include section-relative addends and internal linker names. '
                              'A mismatch is inconclusive about C source identity and remains a closed gate. '
                              'Matching loader ABI alone does not establish CFI or behavioral equivalence.'}
