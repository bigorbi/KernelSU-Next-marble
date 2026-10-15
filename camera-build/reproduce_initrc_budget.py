#!/usr/bin/env python3
"""Offline-only: emulate the captured libbase read loop with a modeled KSU stream.

No kernel compilation, device calls or execution of init commands. The actual
ARM64 loop is emulated; file reads and injected bytes are a deterministic model.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2
from unicorn.arm64_const import UC_ARM64_REG_X19, UC_ARM64_REG_X20, UC_ARM64_REG_X22
from unicorn.arm64_const import UC_ARM64_REG_X24, UC_ARM64_REG_X30, UC_ARM64_REG_PC

LIBBASE_SHA = 'd32279af8f0b6944dc9622ee3f6b4831da2b6bdb490e6e5450cf2ee7a8b5d455'
PHYSICAL_SIZE, STATIC_SIZE = 58427, 355
IMAGE, OBJECT, BUFFER = 0x1000000, 0x2000000, 0x2100000
ENTRY, STOP, READ_PLT = 0x102a8, 0x1030c, 0x2c1e0


def run(data, custom, advertise_custom):
    assert hashlib.sha256(data).hexdigest() == LIBBASE_SHA, 'Unexpected reader binary'
    headers = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    assert headers[1:3] == (3, 183), 'Expected ARM64 shared ELF'
    loads = [struct.unpack_from('<IIQQQQQQ', data, headers[5] + i * headers[9])
             for i in range(headers[10])]
    loads = [p for p in loads if p[0] == 1]
    image_size = (max(p[3] + p[6] for p in loads) + 4095) & -4096
    assert image_size < 8 * 1024**2
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(IMAGE, image_size)
    for p in loads:
        uc.mem_write(IMAGE + p[3], data[p[2]:p[2] + p[5]])
    uc.mem_map(OBJECT, 4096)
    uc.mem_map(BUFFER, 0x20000)
    # Begin after libc++ allocation, with a long-string object and st_size in x22.
    uc.mem_write(OBJECT, struct.pack('<QQQ', 0x20001, 0, BUFFER))
    budget = PHYSICAL_SIZE + STATIC_SIZE + (len(custom) if advertise_custom else 0)
    for register, value in [(UC_ARM64_REG_X19, OBJECT), (UC_ARM64_REG_X20, 3),
                            (UC_ARM64_REG_X22, budget)]:
        uc.reg_write(register, value)
    physical_pos = static_pos = custom_pos = 0
    calls = []

    def callback(machine, address, size, user):
        nonlocal physical_pos, static_pos, custom_pos
        if address != IMAGE + READ_PLT:
            return
        request = machine.reg_read(UC_ARM64_REG_X2)
        target = machine.reg_read(UC_ARM64_REG_X1)
        assert 0 <= request <= 0x20000
        before_custom = custom_pos
        if physical_pos < PHYSICAL_SIZE:
            count = min(request, PHYSICAL_SIZE - physical_pos)
            payload = b'P' * count
            physical_pos += count
        else:
            # Pinned read_iter_proxy returns physical reads immediately; at EOF
            # copy_to_iter consumes static bytes, then only remaining custom room.
            count = min(request, STATIC_SIZE - static_pos)
            payload = b'S' * count
            static_pos += count
            extra = min(request - count, len(custom) - custom_pos)
            payload += custom[custom_pos:custom_pos + extra]
            custom_pos += extra
        assert BUFFER <= target <= BUFFER + 0x20000 - len(payload)
        if payload:
            machine.mem_write(target, payload)
        calls.append({'requested': request, 'returned': len(payload),
                      'custom_bytes': custom_pos - before_custom})
        machine.reg_write(UC_ARM64_REG_X0, len(payload))
        machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_X30))

    uc.hook_add(UC_HOOK_CODE, callback)
    uc.emu_start(IMAGE + ENTRY, IMAGE + STOP, count=1000)
    assert uc.reg_read(UC_ARM64_REG_PC) == IMAGE + STOP
    received = uc.reg_read(UC_ARM64_REG_X24)
    assert received == budget
    if advertise_custom and custom:
        assert bytes(uc.mem_read(BUFFER + PHYSICAL_SIZE + STATIC_SIZE, len(custom))) == custom
    return {'advertised_size': budget, 'custom_length': len(custom),
            'custom_bytes_delivered': custom_pos, 'read_calls': calls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captured_libbase', type=Path)
    parser.add_argument('guard_template', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binary, guard = args.captured_libbase.read_bytes(), args.guard_template.read_bytes()
    assert len(guard) == 233
    cases = {name: run(binary, custom, corrected) for name, custom, corrected in [
        ('shipped_stat_233_byte_guard', guard, False),
        ('corrected_stat_233_byte_guard', guard, True),
        ('shipped_stat_one_byte_fragment', b'X', False),
        ('empty_fragment_builtin_unchanged', b'', True)]}
    assert cases['shipped_stat_233_byte_guard']['custom_bytes_delivered'] == 0
    assert cases['corrected_stat_233_byte_guard']['custom_bytes_delivered'] == 233
    assert cases['shipped_stat_one_byte_fragment']['custom_bytes_delivered'] == 0
    assert cases['empty_fragment_builtin_unchanged']['advertised_size'] == 58782
    result = {'status': 'FOUR_OFFLINE_READER_CASES_PASS', 'libbase_sha256': LIBBASE_SHA,
              'physical_init_rc_size': PHYSICAL_SIZE, 'static_kernel_rc_size': STATIC_SIZE,
              'cases': cases, 'scope': 'Actual reader loop; modeled read/syscall and KSU stream. No kernel or device validation.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
