#!/usr/bin/env python3
"""Manual runner-only KSU/SUSFS size correction. No packaging/device operations.

Reuses the already validated kernel reconstruction, not the camera FullLTO
override. No image is uploaded: current-boot backup/rollback and repack review
are still required. Checksums of the withheld build products are diagnostic.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import ci
from initrc_regression import run as regress, SOURCE_BLOB, SOURCE_SHA
from install_toolchains import install, LLVM_SHA

SUSFS_SHA = 'ca7093a1a26f7911987110c34a1352d74cfb1d09'
SUSFS_PATCH_SHA = '6e5b676c8e3a30f722d0fef20d131345c8889aa5e9bcf9aaeb29c77759ed621f'
PATCH = ci.ROOT / 'ksu-susfs-initrc-size.patch'
REFERENCE = ci.ROOT / 'evidence/kernel-initrc-reference.json'
MANAGER = ci.KERNEL / 'KernelSU-Next'
RELATIVE = 'kernel/runtime/ksud_integration.c'
REQUIRED_CONFIG = {'CONFIG_KSU': 'y', 'CONFIG_KSU_SUSFS': 'y',
                   'CONFIG_MODVERSIONS': 'y', 'CONFIG_CFI_CLANG': 'y',
                   'CONFIG_CFI_CLANG_SHADOW': 'y', 'CONFIG_CFI_PERMISSIVE': 'n',
                   'CONFIG_LTO_CLANG_THIN': 'y', 'CONFIG_LTO_CLANG_FULL': 'n',
                   'CONFIG_ARM64_BTI_KERNEL': 'y', 'CONFIG_UBSAN_TRAP': 'y',
                   'CONFIG_MODULE_FORCE_LOAD': 'n', 'CONFIG_MODULE_FORCE_UNLOAD': 'n'}


def reference():
    return json.loads(REFERENCE.read_text())


def init():
    if os.environ.get('CAMERA_SUSFS_REF') != SUSFS_SHA or not ci.mode():
        raise ValueError('Only the recorded exploratory SUSFS revision is supported')
    ci.init()
    ci.write('kernel-fix-status.json', {
        'status': 'NOT_BUILT', 'kernel_checks_passed': False,
        'boot_publication_allowed': False, 'installation_approved': False,
        'camera_rebuilt': False, 'release_compatibility_verified': False,
        'blockers': ['Installed boot image and kernel rollback not verified',
                     'Historical SUSFS SHA remains unresolved',
                     'No boot-image repack/signature/AVB review performed']})
    if ci.digest(ci.ROOT / 'running-config.txt') != reference()['reference_config_sha256']:
        raise ValueError('Running configuration evidence changed')


def toolchain():
    downloads = ci.ROOT / 'downloads'
    downloads.mkdir(exist_ok=True)
    (ci.ROOT / 'toolchains').mkdir(exist_ok=True)
    archive = downloads / 'LLVM-22.1.8-Linux-X64.tar.xz'
    ci.run(['curl', '-fL', '--retry', '3', '-o', archive,
            'https://github.com/llvm/llvm-project/releases/download/llvmorg-22.1.8/' + archive.name],
           'kernel-llvm-download')
    install('llvm-22.1.8', archive,
            ['22.1.8', 'ca7933e47d3a3451d81e72ac174dcb5aa28b59d1'], expected=LLVM_SHA)
    archive.unlink()
    shutil.copyfile(ci.ROOT / 'toolchains/llvm-22.1.8.verified.json', ci.REPORTS / 'toolchain.json')


def sources():
    ci.sources()
    source = ci.read('sources.json')
    if source['susfs_sha'] != SUSFS_SHA or source['susfs_patch_sha256'] != SUSFS_PATCH_SHA:
        raise ValueError('Recorded integration differs')
    file = MANAGER / RELATIVE
    if ci.git(MANAGER, 'rev-parse', 'HEAD') != ci.MANAGER_SHA or ci.digest(file) != SOURCE_SHA:
        raise ValueError('Wrong KernelSU source')
    if ci.git(MANAGER, 'hash-object', RELATIVE) != SOURCE_BLOB or ci.git(MANAGER, 'status', '--porcelain'):
        raise ValueError('KernelSU checkout is not the clean pinned input')
    regress(file, ci.REPORTS / 'initrc-host-regression.json')
    for args in [['--check'], []]:
        ci.run(['git', '-C', MANAGER, 'apply', *args, PATCH], 'kernel-initrc-patch')
    if ci.git(MANAGER, 'diff', '--name-only', 'HEAD').splitlines() != [RELATIVE]:
        raise ValueError('Correction modified unexpected KernelSU files')
    (ci.REPORTS / 'kernelsu-only.patch').write_text(ci.git(MANAGER, 'diff', 'HEAD') + '\n')
    shutil.copyfile(PATCH, ci.REPORTS / PATCH.name)
    # Preserve the original boot-kernel CFI helper. The corrected helper belonged
    # to the separate Clang21 camera final link, not this LLVM22 boot build.
    helper = ci.KERNEL / 'scripts/generate_cfi_kallsyms.pl'
    if ci.digest(helper) != 'a4a8eb23cdbca209fb8846d153e7cc03a5d438cd239c0cf7852f2206de25164e':
        raise ValueError('Unrelated kernel CFI helper change')
    source.update(initrc_patch_sha256=ci.digest(PATCH),
                  initrc_original_sha256=SOURCE_SHA, initrc_corrected_sha256=ci.digest(file),
                  unchanged_camera_sha256=reference()['unchanged_camera_sha256'],
                  kernel_cfi_helper_sha256=ci.digest(helper))
    ci.write('sources.json', source)
    # Include complete hashes for the copied SUSFS inputs, which git diff alone
    # omits when files were originally untracked.
    copied = {}
    for directory in ['fs', 'include']:
        for path in sorted((ci.WORK / 'susfs/kernel_patches' / directory).rglob('*')):
            if path.is_file():
                relative = path.relative_to(ci.WORK / 'susfs/kernel_patches')
                destination = ci.KERNEL / relative
                if ci.digest(path) != ci.digest(destination):
                    raise ValueError('Copied SUSFS input differs: ' + str(relative))
                copied[str(relative)] = ci.digest(destination)
    ci.write('susfs-copied-inputs.json', copied)


def config():
    ci.config()
    actual = ci.config_values(ci.KOUT / '.config')
    for key, expected in REQUIRED_CONFIG.items():
        if actual.get(key, 'n') != expected:
            raise ValueError('Required security/build option differs: ' + key)


def build():
    status = ci.read('kernel-fix-status.json')
    status['status'] = 'BUILDING'
    ci.write('kernel-fix-status.json', status)
    ci.kernel()  # Genuine vmlinux, module table and generated headers; ThinLTO.
    ci.run(ci.make_args() + ['Image'], 'kernel-image-build', ci.environment('llvm-22.1.8'))
    status['status'] = 'BUILT_NOT_AUDITED'
    ci.write('kernel-fix-status.json', status)


def symvers(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        row = line.split()
        if len(row) < 4 or row[1] in values:
            raise ValueError('Malformed/duplicate symbol table row')
        values[row[1]] = [row[0].removeprefix('0x'), row[3], row[4] if len(row) > 4 else '', row[2]]
    return values


def compare_symbols(actual, ref):
    original = ref['reference_exports']
    differences = {name: {'baseline': original.get(name), 'rebuilt': actual.get(name)}
                   for name in original.keys() | actual.keys() if original.get(name) != actual.get(name)}
    imports = {name: {'official': crc, 'rebuilt': actual.get(name)}
               for name, crc in ref['official_kernel_imports'].items()
               if name not in actual or actual[name][0] != crc}
    return {'baseline_export_differences': differences, 'official_kernel_import_mismatches': imports,
            'all_baseline_exports_match': not differences,
            'official_kernel_imports_match': not imports, 'official_module_count': len(ref['official_modules'])}


def audit():
    checks = {}
    ref = reference()
    config()
    checks['all_config_options_and_natural_release'] = True
    image, elf = ci.KOUT / 'arch/arm64/boot/Image', ci.KOUT / 'vmlinux'
    table = compare_symbols(symvers(ci.KOUT / 'Module.symvers'), ref)
    ci.write('kernel-symbol-comparison.json', table)
    checks['all_baseline_exports_match'] = table['all_baseline_exports_match']
    checks['official_kernel_imports_match'] = table['official_kernel_imports_match']
    # Independent check of the configuration embedded in the produced Image.
    embedded = subprocess.check_output([str(ci.KERNEL / 'scripts/extract-ikconfig'), str(image)])
    (ci.REPORTS / 'embedded-config.txt').write_bytes(embedded)
    checks['embedded_config_matches_generated'] = embedded == (ci.KOUT / '.config').read_bytes()
    with image.open('rb') as stream:
        header = stream.read(64)
    checks['raw_arm64_image_magic'] = header[56:60] == b'ARM\x64'
    bin_dir = ci.ROOT / 'toolchains/llvm-22.1.8/bin'
    command_path = ci.KOUT / 'drivers/kernelsu/runtime/.ksud_integration.o.cmd'
    if not command_path.is_file():
        raise ValueError('Missing real KSU compile command')
    command = command_path.read_text()
    (ci.REPORTS / 'ksud-compile-command.txt').write_text(command)
    checks['compiled_callback_retains_thinlto_and_cfi'] = all(flag in command for flag in
            ['-flto=thin', '-fsanitize=cfi', '-fsanitize-cfi-cross-dso',
             '-fno-sanitize-cfi-canonical-jump-tables']) and not any(flag in command for flag in
            ['-fno-sanitize=cfi', '-fsanitize-recover=cfi', '-ftrap-function='])
    ci.run([bin_dir / 'llvm-nm', '-n', elf], 'kernel-nm')
    names = {line.split()[-1] for line in (ci.LOGS / 'kernel-nm.log').read_text().splitlines() if line.split()}
    checks['cfi_dispatch_and_trapping_runtime_present'] = all(name in names for name in
            ['__cfi_check', '__cfi_slowpath', '__ubsan_handle_cfi_check_fail_abort'])
    checks['cfi_slowpath_export_crc_unchanged'] = all(name in ref['reference_exports'] and
            symvers(ci.KOUT / 'Module.symvers').get(name) == ref['reference_exports'][name]
            for name in ['__cfi_slowpath', '__ubsan_handle_cfi_check_fail_abort'])
    ci.run([bin_dir / 'llvm-objdump', '-d', '--disassemble-symbols=ksu_handle_vfs_fstat,__cfi_check,__cfi_slowpath,__ubsan_handle_cfi_check_fail_abort', elf],
           'kernel-cfi-and-size-disassembly')
    # This checks build evidence, not an assertion that the new kernel has booted.
    build_log = (ci.LOGS / 'kernel-build.log').read_text()
    checks['compiled_ksu_version_and_tag'] = ('-- KernelSU-Next version: 33321' in build_log and
                                             '-- KernelSU-Next tag: v3.4.0' in build_log)
    checks['pinned_integration_and_only_reviewed_ksu_change'] = (
        ci.git(MANAGER, 'diff', '--name-only', 'HEAD').splitlines() == [RELATIVE] and
        ci.digest(MANAGER / RELATIVE) == ci.read('sources.json')['initrc_corrected_sha256'])
    ci.write('kernel-build-products.json', {str(path.relative_to(ci.KOUT)):
             {'size': path.stat().st_size, 'sha256': ci.digest(path)} for path in
             [image, elf, ci.KOUT / 'System.map', ci.KOUT / 'Module.symvers',
              ci.KOUT / '.config', ci.KOUT / 'include/generated/compile.h',
              ci.KOUT / 'include/generated/autoconf.h', ci.KOUT / 'include/generated/utsrelease.h']})
    shutil.copyfile(ci.KOUT / 'include/generated/compile.h', ci.REPORTS / 'compile.h')
    shutil.copyfile(ci.KOUT / 'include/generated/utsrelease.h', ci.REPORTS / 'utsrelease.h')
    ci.write('running-versus-build.json', {
        'running_banner': (ci.ROOT / 'evidence/running-kernel-banner.txt').read_text().strip(),
        'rebuilt_compile_header': (ci.KOUT / 'include/generated/compile.h').read_text(),
        'intended_source_correction': RELATIVE + ': checked built-in plus cached custom RC size',
        'configuration_differences': ci.read('configuration.json')['differences'],
        'historical_susfs_exact': False, 'running_kernel_binary_available': False,
        'exact_running_binary_diff_available': False,
        'known_nonfunctional_differences': ['Build timestamp and possibly build counter',
                                          'ThinLTO layout may change after the source correction'],
        'unchanged_camera_sha256': ref['unchanged_camera_sha256']})
    status = ci.read('kernel-fix-status.json')
    status.update(status='STATIC_CHECKS_PASS_BOOT_WITHHELD' if all(checks.values()) else 'FAIL',
                  kernel_checks_passed=all(checks.values()), checks=checks,
                  runtime_cfi_verified=False, guard_delivery_on_device_verified=False)
    ci.write('kernel-fix-status.json', status)
    if not all(checks.values()):
        raise ValueError('Corrected kernel failed checks; inspect diagnostic artifacts')


def finalize():
    # Always emit hashes, even after failure. Never call camera build/finalize.
    ci.LOGS.mkdir(parents=True, exist_ok=True)
    ci.REPORTS.mkdir(parents=True, exist_ok=True)
    if not (ci.REPORTS / 'kernel-fix-status.json').exists():
        ci.write('kernel-fix-status.json', {'status': 'FAILED_BEFORE_INIT', 'boot_publication_allowed': False})
    if (ci.REPORTS / 'kernel-fix-failure.json').exists():
        status = ci.read('kernel-fix-status.json')
        status.update(status='FAILED_' + ci.read('kernel-fix-failure.json')['stage'].upper(),
                      kernel_checks_passed=False, boot_publication_allowed=False)
        ci.write('kernel-fix-status.json', status)
    if (ci.KOUT / 'Module.symvers').is_file():
        shutil.copyfile(ci.KOUT / 'Module.symvers', ci.REPORTS / 'kernel-Module.symvers')
    ci.write('available-build-products.json', {name: {'size': path.stat().st_size, 'sha256': ci.digest(path)}
             for name in ['arch/arm64/boot/Image', 'vmlinux', 'Module.symvers', 'System.map', '.config']
             if (path := ci.KOUT / name).is_file()})
    for directory in [ci.LOGS, ci.REPORTS]:
        files = sorted(p for p in directory.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
        (directory / 'SHA256SUMS').write_text(''.join(ci.digest(p) + '  ' + str(p.relative_to(directory)) + '\n' for p in files))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        status = ci.read('kernel-fix-status.json')
        with open(summary, 'a') as stream:
            stream.write('## Kernel init-RC correction\n\n' + status['status'] +
                         '\n\nDiagnostic reports only. Boot/Image/vmlinux publication withheld. '
                         'Camera artifact unchanged. Exploratory SUSFS; no installation approval.\n')


STAGES = {'init': init, 'toolchain': toolchain, 'sources': sources, 'config': config,
          'build': build, 'audit': audit, 'finalize': finalize}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({'stage': args.stage, 'kernel_sha': ci.KERNEL_SHA,
                          'manager_sha': ci.MANAGER_SHA, 'susfs_sha': SUSFS_SHA,
                          'toolchain': 'LLVM 22.1.8', 'lto': 'thin', 'make_jobs': 2,
                          'camera_rebuild': False, 'boot_publication_allowed': False,
                          'network_or_compilation': False}))
    elif os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Build stages run only on GitHub Actions; use --dry-run locally')
    else:
        try:
            STAGES[args.stage]()
        except Exception as error:
            ci.write('kernel-fix-failure.json', {'stage': args.stage, 'error': str(error)})
            raise
