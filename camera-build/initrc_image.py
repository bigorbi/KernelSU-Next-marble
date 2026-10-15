#!/usr/bin/env python3
"""Retain a raw Image for review, reusing the frozen corrected-kernel builder.

No boot container, release, device operation or installation approval. A rebuilt
Image with different hashes is quarantined and the workflow fails closed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct

import kernel_initrc_fix as fix

ci = fix.ci
REFERENCE = ci.ROOT / 'evidence/initrc-image-reference.json'


def reference():
    return json.loads(REFERENCE.read_text())


def preflight():
    ref = reference()
    manifest = ci.ROOT / 'evidence/kernel-initrc-inputs.sha256'
    if ci.digest(manifest) != ref['reviewed_inputs_manifest_sha256']:
        raise ValueError('Reviewed input manifest changed')
    count = 0
    for row in manifest.read_text().splitlines():
        expected, relative = row.split('  ', 1)
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or ci.digest(ci.REPO / path) != expected:
            raise ValueError('Reviewed build input changed: ' + relative)
        count += 1
    # This is an explicit reproducibility attempt, not the actual run date.
    # kheaders mtimes were not normalized in the earlier run; equality is NOT
    # presumed even with the same compile.h timestamp.
    if (os.environ.get('KBUILD_BUILD_TIMESTAMP') != ref['reproduction_timestamp'] or
            os.environ.get('KBUILD_BUILD_VERSION') != '1'):
        raise ValueError('Unexpected reproducibility metadata')
    ci.write('image-publication.json', {
        'status': 'NOT_BUILT', 'reference_run': ref['run'],
        'raw_image_identity_verified': False, 'installation_approved': False,
        'boot_image_created': False, 'reviewed_input_files_verified': count,
        'reproduction_timestamp': ref['reproduction_timestamp'],
        'timestamp_is_actual_run_time': False,
        'github_run': os.environ.get('GITHUB_RUN_ID'),
        'github_commit': os.environ.get('GITHUB_SHA')})


def function(text, name):
    match = re.search(r'^[0-9a-f]+ <' + re.escape(name) + r'>:\n.*?(?=\n\n|\Z)',
                      text, re.M | re.S)
    if not match:
        raise ValueError('Missing emitted function: ' + name)
    return match[0]


def emulate_arithmetic(text, nm):
    """Execute actual emitted arithmetic; loader/printk mocked, cleanup checked.

    Derived from the independent run-38759893534 verifier. Addresses come from
    THIS binary. An unrecognized compiler layout fails, never skips the test.
    Not an emulation of the entire kernel, identity filter or RC loader.
    """
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_X20, UC_ARM64_REG_PC
    body = function(text, 'ksu_handle_vfs_fstat')
    rows = re.findall(r'^([0-9a-f]+):\s+([0-9a-f]{8})\s+(.*)$', body, re.M)
    if not rows:
        raise ValueError('No emitted instructions')
    start = int(rows[0][0], 16)
    bias = 0x1000000 - (start & ~4095)
    words = {int(a, 16): (int(w, 16), op) for a, w, op in rows}
    begin = next(a for a, (_, op) in words.items()
                 if op.startswith('bl\t') and '<load_module_rc_once>' in op)
    release = next(a for a, (_, op) in words.items()
                   if op.startswith('bl\t') and '<fput_many>' in op) - 8
    mocks = {a for a, (_, op) in words.items() if op.startswith('bl\t') and
             ('<load_module_rc_once>' in op or '<printk>' in op)}
    addresses = re.findall(r'^([0-9a-f]+) b module_rc_len$', nm, re.M)
    if len(addresses) != 1:
        raise ValueError('Ambiguous actual module_rc_len symbol')
    length_addr = int(addresses[0], 16)
    u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    code_pages = {((a + bias) & ~4095) for a in words}
    for page in sorted(code_pages | {((length_addr + bias) & ~4095)}):
        u.mem_map(page, 4096)
    for address, (word, _) in words.items():
        u.mem_write(address + bias, struct.pack('<I', word))
    stat = 0x4000000
    u.mem_map(stat, 4096)
    state = {}

    def hook(uc, pc, size, unused):
        address = pc - bias
        if address == release:
            state['released'] = True
            uc.emu_stop()
        elif address in mocks:
            if address == begin:
                state['loaded'] += 1
                uc.mem_write(length_addr + bias, struct.pack('<Q', state['custom']))
            uc.reg_write(UC_ARM64_REG_PC, pc + 4)

    u.hook_add(UC_HOOK_CODE, hook)
    maximum = (1 << 63) - 1
    cases = [(58427, 233), (58427, 0), (0, 1), (-1, 233),
             (maximum - 354, 0), (maximum - 355, 1),
             (maximum - 588, 233), (0, maximum), (maximum, 233)]
    for physical, custom in cases:
        state.clear()
        state.update(custom=custom, loaded=0)
        u.mem_write(stat, struct.pack('<q', physical))
        u.reg_write(UC_ARM64_REG_X20, stat)
        u.emu_start(begin + bias, max(code_pages) + 4096, count=200)
        actual = struct.unpack('<q', u.mem_read(stat, 8))[0]
        expected = physical + 355 + custom
        if physical < 0 or expected > maximum:
            expected = physical
        if actual != expected or not state.get('released') or state['loaded'] != 1:
            raise ValueError(f'Emitted fstat case failed: {physical}, {custom}')
    return {'cases_passed': len(cases), 'module_rc_len_address': hex(length_addr),
            'scope': 'Actual emitted arithmetic only; calls mocked; cleanup join reached'}


def compare_products(actual, expected):
    return {key: {'reference': expected.get(key), 'rebuilt': actual.get(key)}
            for key in sorted(actual.keys() | expected.keys())
            if actual.get(key) != expected.get(key)}


def retain():
    ref = reference()
    status = ci.read('kernel-fix-status.json')
    checks = status.get('checks', {})
    if (status.get('status') != 'STATIC_CHECKS_PASS_BOOT_WITHHELD' or
            status.get('kernel_checks_passed') is not True or
            sorted(checks) != ref['required_checks'] or
            any(value is not True for value in checks.values()) or
            (ci.REPORTS / 'kernel-fix-failure.json').exists()):
        raise ValueError('All original ABI/config/CFI/source gates are mandatory')
    source = ci.read('sources.json')
    # Only workflow/run identity may change; compare every historical source key.
    source_differences = {key: [value, source.get(key)]
                          for key, value in ref['source_manifest'].items()
                          if key not in ['workflow_sha', 'workflow_run'] and source.get(key) != value}
    if source_differences:
        raise ValueError('Pinned integration changed: ' + json.dumps(source_differences))
    if (ci.digest(ci.REPORTS / 'kernel-integration.patch') != ref['integration_patch_sha256'] or
            ci.read('susfs-copied-inputs.json') != ref['susfs_copied_inputs']):
        raise ValueError('Kernel/SUSFS integration differs from validated run')
    independent = fix.compare_symbols(fix.symvers(ci.KOUT / 'Module.symvers'), fix.reference())
    if (not independent['all_baseline_exports_match'] or not independent['official_kernel_imports_match'] or
            independent != ci.read('kernel-symbol-comparison.json')):
        raise ValueError('Independent CRC audit failed')
    command = (ci.REPORTS / 'ksud-compile-command.txt').read_text().splitlines()[0]
    if not all(flag in command.split() for flag in ['-flto=thin', '-fsanitize=cfi',
                   '-fsanitize-cfi-cross-dso', '-fno-sanitize-cfi-canonical-jump-tables']):
        raise ValueError('Actual compile command lacks enforcing ThinLTO/CFI flags')
    text = (ci.LOGS / 'kernel-cfi-and-size-disassembly.log').read_text()
    abort = function(text, '__ubsan_handle_cfi_check_fail_abort')
    if '<panic>' not in abort or 'udf' not in abort:
        raise ValueError('CFI failure must still panic/trap')
    emulation = emulate_arithmetic(text, (ci.LOGS / 'kernel-nm.log').read_text())
    products = ci.read('kernel-build-products.json')
    # Independently rehash ALL real outputs before retaining any binary.
    for name, value in products.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe product path')
        path = ci.KOUT / relative
        if value != {'size': path.stat().st_size, 'sha256': ci.digest(path)}:
            raise ValueError('Build-product record no longer matches bytes: ' + name)
    differences = compare_products(products, ref['products'])
    result = ci.read('image-publication.json')
    result.update(status='EXACT_REFERENCE_PRODUCTS_RETAINED_FOR_REVIEW' if not differences else
                  'HASH_MISMATCH_QUARANTINED_NOT_REFERENCE_BINARY',
                  static_audits_passed=True, additional_fstat_execution=emulation,
                  raw_image_identity_verified=not differences,
                  reference_product_differences=differences, actual_products=products,
                  camera_rebuilt=False, bootloader_acceptance_verified=False,
                  installation_approved=False, boot_image_created=False)
    ci.write('image-publication.json', result)
    directory = ci.OUT / ('reviewed-raw-image' if not differences else 'quarantined-raw-image')
    directory.mkdir(exist_ok=False)
    shutil.copyfile(ci.KOUT / 'arch/arm64/boot/Image', directory / 'Image')
    if ci.digest(directory / 'Image') != products['arch/arm64/boot/Image']['sha256']:
        raise ValueError('Retained Image copy does not match the audited source bytes')
    shutil.copyfile(ci.REPORTS / 'image-publication.json', directory / 'REVIEW.json')
    shutil.copyfile(ci.REPORTS / 'sources.json', directory / 'sources.json')
    (directory / 'SHA256SUMS').write_text(''.join(ci.digest(p) + '  ' + p.name + '\n'
        for p in sorted(directory.iterdir()) if p.is_file()))
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as stream:
            stream.write('exact=' + str(not differences).lower() + '\n')
    if differences:
        raise ValueError('Prior product hashes NOT reproduced. Distinct diagnostic Image quarantined; '
                         'no reference-identity or installation approval. Review the new binary.')


def finalize():
    # Reuse the original checksum/failure finalizer without its old diagnostic-
    # only summary, which predates authorized raw Image retention.
    summary = os.environ.pop('GITHUB_STEP_SUMMARY', None)
    try:
        fix.finalize()
    finally:
        if summary is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = summary
    if summary:
        result = ci.read('image-publication.json') if (ci.REPORTS / 'image-publication.json').exists() else {}
        with open(summary, 'a') as stream:
            stream.write('## Corrected raw Image review\n\n' + result.get('status', 'NOT_RETAINED') +
                '\n\nAll prior ABI/config/CFI gates remain mandatory. Hash mismatch fails the run; '
                'a quarantined diagnostic is a different binary. No boot container, installation '
                'approval, camera rebuild or device action. See image-publication.json and any '
                'image-publication-error.json.\n')


STAGES = {'preflight': preflight, 'retain': retain, 'finalize': finalize}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({'stage': args.stage, 'reference_run': reference()['run'],
                          'network_or_compilation': False, 'installation_approved': False,
                          'boot_image_created': False, 'hash_mismatch_fails_workflow': True}))
    elif os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Runner-only; use --dry-run locally')
    else:
        try:
            STAGES[args.stage]()
        except Exception as error:
            ci.write('image-publication-error.json', {'stage': args.stage, 'error': str(error)})
            raise
