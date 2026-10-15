#!/usr/bin/env python3
"""Compile ONLY an extracted host harness of authenticated KSU RC functions.

No kernel build, device access or network. Run each case in a fresh process to
reset the actual loader's function-static once flag. Test both read proxies.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCE_BLOB = '630c49cd542d20df2fe0849d8c544eccc8b18584'
SOURCE_SHA = 'e9e676e00d2de75885525f58b1c433ea1841c2bec41dc413170dea9f5cb99ad1'
CASES = ['combined', 'empty', 'disabled', 'missing', 'fallback', 'nonregular',
         'allocfail', 'readerror', 'readzero', 'shortread', 'repeated',
         'negative', 'overflow_static', 'overflow_custom', 'oversized_custom',
         'limit_exact', 'invalid_fd', 'unrelated', 'exit_cleanup', 'copy_retry',
         'read_error', 'stat_then_change', 'module_copy_retry', 'zero_count']


def function(source, name):
    # These pinned functions end at a column-zero brace. Fail if absent/ambiguous.
    import re
    matches = list(re.finditer(r'^.*\b' + re.escape(name) + r'\([^;\n]*\)\n\{', source, re.M))
    if len(matches) != 1:
        raise ValueError('Function extraction ambiguous: ' + name)
    start = matches[0].start()
    return source[start:source.index('\n}', matches[0].end()) + 2]


def run(source, output=None):
    data = Path(source).read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if blob != SOURCE_BLOB or hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        raise ValueError('Expected untouched pinned KernelSU source, not a modified copy')
    results = []
    with tempfile.TemporaryDirectory(prefix='ksu-initrc-host-') as temporary:
        temp = Path(temporary)
        target = temp / 'kernel/runtime/ksud_integration.c'
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        patch = ROOT / 'ksu-susfs-initrc-size.patch'
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=temp, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=temp, check=True)
        original, corrected = data.decode(), target.read_text()
        for variant, text in [('original', original), ('corrected', corrected)]:
            builtin = text[text.index('static const char KERNEL_SU_RC[]'):text.index('// clang-format on')]
            proxies = text[text.index('static ssize_t (*orig_read)'):text.index('static bool is_init_rc(')]
            body = ('#include "initrc_mocks.h"\n' + builtin + '\n' + proxies + '\n' +
                    function(text, 'ksu_handle_vfs_fstat') + '\n' +
                    function(text, 'ksu_ksud_exit') + '\n#include "initrc_cases.h"\n')
            program = temp / (variant + '.c')
            program.write_text(body)
            for sanitize in [False, True]:
                binary = temp / (variant + ('-sanitized' if sanitize else '-optimized'))
                flags = ['-O1', '-g', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
                         '-fsanitize=address,undefined'] if sanitize else ['-O2']
                command = [os.environ.get('HOST_TEST_CC', 'cc'), '-std=gnu11',
                           '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                           '-Wno-unused-variable', '-Wno-sign-compare',
                           '-I', str(ROOT / 'tests'), *flags, str(program), '-o', str(binary)]
                subprocess.run(command, check=True)
                cases = ['original_budget'] if variant == 'original' else CASES
                for case in cases:
                    for proxy in ['read', 'iter']:
                        environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',
                                           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
                        subprocess.run([str(binary), case, proxy], check=True, env=environment,
                                       capture_output=True, text=True)
                        results.append({'variant': variant, 'case': case,
                                        'proxy': proxy, 'sanitized': sanitize, 'pass': True})
        report = {'status': 'PASS_HOST_EXTRACTED_C_NOT_KERNEL_COMPILED',
                  'source_git_blob': blob, 'source_sha256': SOURCE_SHA,
                  'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
                  'corrected_source_sha256': hashlib.sha256(corrected.encode()).hexdigest(),
                  'executions': len(results), 'cases': results}
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run(args.source, args.output)
    print(result['status'], result['executions'], 'executions')
