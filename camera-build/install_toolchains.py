#!/usr/bin/env python3
"""Extract into project-local directories only; reject unverified or unsafe archives."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from fetch_android_manifest import ROOT_TREE, COMMIT, object_hash, tree_hash

ROOT = Path(__file__).resolve().parent
LLVM_SHA = 'df0e1ecf16caf3489a272a5eea4eec9b0d82878f6477fa309504f918a0006384'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(manifest, root):
    if manifest['commit'] != COMMIT or manifest['root_tree'] != ROOT_TREE or tree_hash(manifest['root_entries']) != ROOT_TREE:
        raise ValueError('Unauthenticated AOSP root tree')
    subtree = next(x['id'] for x in manifest['root_entries'] if x['name'] == 'clang-r563880c')
    directories = {str(parent) for item in manifest['files'] for parent in PurePosixPath(item['path']).parents}
    nodes = {directory: [] for directory in directories}
    for item in manifest['files']:
        path = PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Unsafe manifest path')
        target = root / item['path']
        data = os.readlink(target).encode() if item['mode'] == '120000' else target.read_bytes()
        if object_hash('blob', data) != item['sha1']:
            raise ValueError('AOSP Git blob checksum mismatch: ' + item['path'])
        if item['mode'] != '120000' and bool(target.stat().st_mode & 0o111) != (item['mode'] == '100755'):
            raise ValueError('Executable mode mismatch: ' + item['path'])
        nodes.setdefault(str(path.parent), []).append({'name': path.name, 'mode': int(item['mode'], 8),
                                                     'type': 'blob', 'id': item['sha1']})
    # Reconstruct every directory tree from verified files, so a shortened manifest cannot pass.
    for name in sorted(nodes, key=lambda value: len(PurePosixPath(value).parts), reverse=True):
        if name == '.':
            continue
        path = PurePosixPath(name)
        nodes.setdefault(str(path.parent), []).append({'name': path.name, 'mode': 0o40000,
                                                     'type': 'tree', 'id': tree_hash(nodes[name])})
    if tree_hash(nodes['.']) != subtree:
        raise ValueError('Incomplete or altered AOSP subtree manifest')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_symlink() or p.is_file()}
    if actual != {item['path'] for item in manifest['files']}:
        raise ValueError('Unexpected extracted files')


def install(name, archive, identity, expected=None, manifest=None):
    destination = ROOT / 'toolchains' / name
    receipt = ROOT / 'toolchains' / (name + '.verified.json')
    if destination.exists() or receipt.exists():
        raise ValueError('Refusing to overwrite existing toolchain: ' + str(destination))
    if not archive.is_file():
        raise FileNotFoundError(archive)
    digest = sha256(archive)
    if expected and digest != expected:
        raise ValueError('Archive SHA256 mismatch: ' + str(archive))
    with tempfile.TemporaryDirectory(dir=ROOT / 'toolchains', prefix='extract-') as temporary:
        temp = Path(temporary)
        with tarfile.open(archive) as tar:
            tar.extractall(temp, filter='data')
        if manifest:
            extracted = temp
            verify_manifest(manifest, extracted)
        else:
            candidates = list(temp.glob('*/bin/clang')) + list(temp.glob('bin/clang'))
            if len(candidates) != 1:
                raise ValueError('Unexpected upstream LLVM archive layout')
            extracted = candidates[0].parent.parent
        version = subprocess.check_output([str(extracted / 'bin/clang'), '--version'], text=True)
        if not all(value in version for value in identity):
            raise ValueError('Compiler identity mismatch: ' + version)
        for executable in ['clang', 'ld.lld', 'llvm-ar', 'llvm-nm', 'llvm-objcopy', 'llvm-objdump',
                           'llvm-readelf', 'llvm-strip', 'llvm-ranlib']:
            if not os.access(extracted / 'bin' / executable, os.X_OK):
                raise ValueError('Required tool missing: ' + executable)
        shutil.move(str(extracted), destination)
    receipt.write_text(json.dumps({'status': 'PASS', 'archive': str(archive), 'sha256': digest,
                                  'upstream_sha256': expected, 'aosp_commit': COMMIT if manifest else None,
                                  'verification': 'Pinned Git tree and all blob hashes' if manifest else 'Upstream SHA256',
                                  'compiler': version}, indent=2) + '\n')
    print('Verified and installed:', name)


def main():
    (ROOT / 'toolchains').mkdir(exist_ok=True)
    archive = ROOT / 'downloads/LLVM-22.1.8-Linux-X64.tar.xz'
    if not (ROOT / 'toolchains/llvm-22.1.8').exists():
        install('llvm-22.1.8', archive, ['22.1.8', 'ca7933e47d3a3451d81e72ac174dcb5aa28b59d1'], expected=LLVM_SHA)
    if not (ROOT / 'toolchains/android-r563880c').exists():
        manifest_path = ROOT / 'downloads/android-r563880c-14054515.manifest.json'
        manifest = json.loads(manifest_path.read_text())
        install('android-r563880c', ROOT / 'downloads/android-r563880c-14054515.tar.gz',
                ['21.0.0', '14054515', 'r563880c', '5e96669f06077099aa41290cdb4c5e6fa0f59349'], manifest=manifest)


if __name__ == '__main__':
    main()
