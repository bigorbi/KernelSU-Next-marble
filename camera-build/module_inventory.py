#!/usr/bin/env python3
"""Read-only ELF64 module inventory and necessary ABI comparison. No CRC rewriting."""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def cstr(data, offset=0):
    return data[offset:].split(b'\0', 1)[0].decode(errors='replace')


def inventory(path):
    data = Path(path).read_bytes()
    assert data[:6] == b'\x7fELF\x02\x01', 'Expected little-endian ELF64'
    elf = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    section_offset, section_size, section_count, names_index = elf[6], elf[11], elf[12], elf[13]
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, section_offset + section_size*i)
                for i in range(section_count)]
    names = sections[names_index]
    names_data = data[names[4]:names[4]+names[5]]
    section_names = [cstr(names_data, s[0]) for s in sections]
    blobs = {n: data[s[4]:s[4]+s[5]] for n, s in zip(section_names, sections) if s[1] != 8}
    symtab = sections[section_names.index('.symtab')]
    strings = sections[symtab[6]]
    strings = data[strings[4]:strings[4]+strings[5]]
    symbols = []
    for offset in range(symtab[4], symtab[4]+symtab[5], symtab[9]):
        name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', data, offset)
        symbols.append({'name': cstr(strings, name), 'binding': info >> 4, 'type': info & 15,
                        'visibility': other & 3, 'section': index, 'value': value, 'size': size})
    versions = blobs.get('__versions', b'')
    assert len(versions) % 64 == 0, 'Unexpected modversion_info size'
    imports = {cstr(versions[i+8:i+64]): f'{struct.unpack_from("<Q", versions, i)[0]:08x}'
               for i in range(0, len(versions), 64)}
    modinfo = sorted(x.decode(errors='replace') for x in blobs.get('.modinfo', b'').split(b'\0') if x)
    absolute_crcs = {s['name'][6:]: f"{s['value'] & 0xffffffff:08x}"
                     for s in symbols if s['name'].startswith('__crc_') and s['section'] == 0xfff1}
    exports = sorted(s['name'][10:] for s in symbols if s['name'].startswith('__ksymtab_'))
    undefined = sorted(s['name'] for s in symbols if s['section'] == 0 and s['name'])
    module_symbol = [s['size'] for s in symbols if s['name'] == '__this_module']
    return {'path': str(Path(path).resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
            'file_size': len(data), 'elf_type': elf[1], 'machine': elf[2], 'modinfo': modinfo,
            'compiler': cstr(blobs.get('.comment', b''), 1), 'imports_crc': imports,
            'undefined': undefined, 'exports': exports, 'absolute_export_crc': absolute_crcs,
            'module_struct_size': module_symbol,
            'sections': {n: {'type': s[1], 'size': s[5], 'flags': s[2], 'alignment': s[8]}
                         for n, s in zip(section_names, sections)},
            'cfi_symbols': sorted(s['name'] for s in symbols if 'cfi' in s['name'].lower()),
            'cfi_symbol_records': [{'name': s['name'], 'binding': s['binding'], 'type': s['type'],
                                    'visibility': s['visibility'], 'defined': s['section'] != 0, 'size': s['size']}
                                   for s in symbols if 'cfi' in s['name'].lower()],
            'signature_trailer': data[-28:] == b'~Module signature appended~\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original')
    parser.add_argument('--baseline')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = {'original': inventory(args.original), 'compatibility': 'NOT DEMONSTRATED'}
    if args.baseline:
        baseline = inventory(args.baseline)
        original = result['original']
        result['baseline'] = baseline
        keys = ['elf_type', 'machine', 'modinfo', 'compiler', 'imports_crc', 'undefined',
                'exports', 'absolute_export_crc', 'module_struct_size', 'signature_trailer']
        result['necessary_checks'] = {key: original[key] == baseline[key] for key in keys}
        result['compatibility'] = 'NECESSARY CHECKS PASS; CFI/layout and provenance review still required' if all(result['necessary_checks'].values()) else 'FAIL'
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print('Original:', result['original']['sha256'])
    print('Imported CRCs:', len(result['original']['imports_crc']))
    print('Exports:', len(result['original']['exports']))
    print('Status:', result['compatibility'])


if __name__ == '__main__':
    main()
