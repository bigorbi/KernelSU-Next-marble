#!/usr/bin/env python3
"""Read-only v3/v4 Android boot-container inventory. Does NOT repack or sign."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(path):
    data = Path(path).read_bytes()
    if len(data) < 4096 or data[:8] != b'ANDROID!':
        raise ValueError('Not a complete Android boot header')
    kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from('<4I', data, 8)
    version = struct.unpack_from('<I', data, 40)[0]
    if version not in (3, 4) or header_size != {3: 1580, 4: 1584}[version]:
        raise ValueError('Unsupported boot header version/size')
    if any(data[24:40]):
        raise ValueError('Nonzero reserved boot-header fields')
    align = lambda x: (x + 4095) & ~4095
    ramdisk_offset = 4096 + align(kernel_size)
    signature_offset = ramdisk_offset + align(ramdisk_size)
    signature_size = struct.unpack_from('<I', data, 1580)[0] if version == 4 else 0
    payload_end = signature_offset + signature_size
    if not kernel_size or payload_end > len(data):
        raise ValueError('Truncated/empty boot payload')
    kernel = data[4096:4096 + kernel_size]
    ramdisk = data[ramdisk_offset:ramdisk_offset + ramdisk_size]
    # No assumption that a matching UTS_RELEASE means the installed kernel matches.
    raw = gzip.decompress(kernel) if kernel[:2] == b'\x1f\x8b' else kernel
    if len(raw) < 64 or raw[56:60] != b'ARM\x64':
        raise ValueError('Expected ARM64 Image payload')
    result = {'container_sha256': sha(data), 'container_size': len(data),
              'header_version': version, 'header_size': header_size, 'page_size': 4096,
              'os_version_encoded': os_version, 'kernel_size': kernel_size,
              'kernel_sha256': sha(kernel), 'raw_kernel_sha256': sha(raw),
              'kernel_compression': 'gzip' if raw is not kernel else 'none',
              'arm64_image_size': struct.unpack_from('<Q', raw, 16)[0],
              'ramdisk_size': ramdisk_size, 'ramdisk_sha256': sha(ramdisk),
              'boot_signature_size': signature_size,
              'boot_signature_sha256': sha(data[signature_offset:payload_end]),
              'linux_banners': [v.decode(errors='replace') for v in
                                re.findall(rb'Linux version [0-9][^\x00\n]+', raw)],
              'avb_footer': None, 'cryptographic_avb_verified': False,
              'installation_approved': False}
    if data[-64:-60] == b'AVBf':
        _, major, minor, original_size, offset, size = struct.unpack_from('>4sIIQQQ', data, len(data)-64)
        if major != 1 or original_size < payload_end or original_size > offset or offset + size > len(data)-64:
            raise ValueError('Invalid AVB footer extents')
        result['avb_footer'] = {'major': major, 'minor': minor, 'original_image_size': original_size,
                                'vbmeta_offset': offset, 'vbmeta_size': size,
                                'vbmeta_sha256': sha(data[offset:offset+size])}
    start = raw.find(b'IKCFG_ST')
    end = raw.find(b'IKCFG_ED', start+8)
    if start >= 0 and end > start:
        config = gzip.decompress(raw[start+8:end])
        result['embedded_config_sha256'] = sha(config)
        result['embedded_ksu_config'] = [line for line in config.decode().splitlines()
                                          if 'CONFIG_KSU' in line]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inventory(args.image), indent=2) + '\n')
