#!/usr/bin/env python3
"""Compile the actual reviewed C functions, with explicit host-only API mocks."""
from pathlib import Path
import subprocess
import hashlib
import os

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / 'camera-work/modules-patched/qcom/opensource/camera-kernel/drivers'
BASE = ROOT.parent / 'camera-work/modules-baseline/qcom/opensource/camera-kernel/drivers'


def function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


names = ['cam_packet_util_get_cmd_mem_addr', 'cam_packet_util_validate_cmd_desc',
         'cam_packet_util_get_kmd_buffer']
reviewed = (SRC / 'cam_utils/cam_packet_util.c').read_text()
original = (BASE / 'cam_utils/cam_packet_util.c').read_text()
body = '\n'.join(function(reviewed, 'int ' + name + '(') for name in names)
old = function(original, 'int cam_packet_util_get_kmd_buffer(').replace(
    'cam_packet_util_get_kmd_buffer(', 'original_get_kmd_buffer(', 1)
frame = function((SRC / 'cam_isp/isp_hw_mgr/cam_ife_hw_mgr.c').read_text(),
                 'static int cam_ife_mgr_util_insert_frame_header(')
prefix = (ROOT / 'tests/mocks.h').read_text()
suffix = (ROOT / 'tests/test_kmd.c').read_text()
generated = ROOT / 'tests/generated_test.c'
generated.write_text(prefix + '\n' + body + '\n' + old + '\n' + frame + '\n' + suffix)
print('Extracted source SHA256:', hashlib.sha256((body + frame).encode()).hexdigest(), flush=True)
for suffix, flags in [('asan', ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-no-pie']),
                      ('optimized', ['-O2'])]:
    binary = ROOT / 'tests' / ('test-' + suffix)
    command = ['gcc', '-std=gnu11', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-type-limits', '-Wno-unused-parameter', *flags,
               str(generated), '-o', str(binary)]
    print('Build:', ' '.join(command), flush=True)
    subprocess.run(command, check=True)
    env = dict(os.environ)
    if suffix == 'asan':
        # This managed environment traces child processes; LSan cannot run under ptrace.
        env['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'
        env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
        print('ASan/UBSan enabled; LSan disabled because ptrace is active.', flush=True)
    subprocess.run([str(binary)], check=True, env=env)
