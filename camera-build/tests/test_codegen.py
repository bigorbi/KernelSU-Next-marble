"""Independent ELF fixtures for instruction masking and CFI generator replay."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deep_compare import mask_instruction, union_size
from cfi_pointers import pointer_inventory
from elf_probe import Elf

ROOT = Path(__file__).resolve().parents[1]
KERNEL_SHA = "5c59cd9a314704d30f3f66004183426da23410e1"
SCRIPT_SHA = "a4a8eb23cdbca209fb8846d153e7cc03a5d438cd239c0cf7852f2206de25164e"


def pinned_script():
    for repo in [
        ROOT.parent / "camera-work/kernel",
        ROOT.parent.parent / "android_kernel_xiaomi_sm8450",
    ]:
        if (repo / ".git").exists():
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "show",
                    KERNEL_SHA + ":scripts/generate_cfi_kallsyms.pl",
                ],
                capture_output=True,
            )
            if (
                result.returncode == 0
                and hashlib.sha256(result.stdout).hexdigest() == SCRIPT_SHA
            ):
                return result.stdout
    return None


class CodeAccountingTests(unittest.TestCase):
    def test_aliases_and_nested_ranges_do_not_double_count(self):
        self.assertEqual(union_size([(8, 16), (8, 16), (10, 12), (20, 24)]), 12)

    def test_relocation_mask_preserves_branch_opcode(self):
        self.assertNotEqual(
            mask_instruction(0x94000000, 283), mask_instruction(0x14000000, 283)
        )

    def test_relocation_mask_preserves_registers(self):
        self.assertNotEqual(
            mask_instruction(0x90000010, 275), mask_instruction(0x90000011, 275)
        )
        self.assertEqual(
            mask_instruction(0x90000010, 275), mask_instruction(0xB0123410, 275)
        )

    def test_unsupported_relocation_fails(self):
        with self.assertRaises(KeyError):
            mask_instruction(0, 999)


@unittest.skipUnless(
    all(
        shutil.which(x)
        for x in ["llvm-mc", "llvm-objdump", "llvm-readelf", "llvm-nm", "perl", "patch"]
    ),
    "LLVM/Perl/patch tools required",
)
class GeneratorTests(unittest.TestCase):
    def test_actual_pinned_generator_and_bti_fix(self):
        source = pinned_script()
        if source is None:
            self.skipTest("Pinned kernel helper unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            script = root / "scripts/generate_cfi_kallsyms.pl"
            script.write_bytes(source)
            assembly = root / "fixture.s"
            assembly.write_text(
                ".text\n.globl callback\n.type callback,@function\ncallback:\nbti c\nret\n.size callback,.-callback\n.globl __cfi_jt_start\n__cfi_jt_start:\nbti c\nb callback\n.globl __cfi_jt_end\n__cfi_jt_end:\n.data\n.globl operations\n.type operations,@object\noperations:\n.xword callback.cfi_jt\n.size operations,.-operations\n.hidden callback.cfi_jt\n"
            )
            obj = root / "fixture.o"
            subprocess.run(
                [
                    "llvm-mc",
                    "-triple=aarch64-linux-gnu",
                    "-filetype=obj",
                    str(assembly),
                    "-o",
                    str(obj),
                ],
                check=True,
            )
            env = dict(
                os.environ,
                READELF=shutil.which("llvm-readelf"),
                OBJDUMP=shutil.which("llvm-objdump"),
                NM=shutil.which("llvm-nm"),
            )

            def generated(environment):
                result = subprocess.check_output(
                    ["perl", str(script), "--module", str(obj)],
                    env=environment,
                    text=True,
                )
                return int(
                    re.search(r"callback\.cfi_jt = \. \+ 0x([a-f0-9]+)", result)[1], 16
                )

            self.assertEqual(generated(env), 12)  # broken: B instead of BTI at 8
            # Independently replay historical disassembly spelling on the same ELF.
            wrapper = root / "legacy-objdump"
            wrapper.write_text(
                "#!"
                + sys.executable
                + "\nimport subprocess,sys\ns=subprocess.check_output("
                + repr([shutil.which("llvm-objdump")])
                + '+sys.argv[1:],text=True)\nprint(s.replace("bti\\tc", "hint\\t#34"),end="")\n'
            )
            wrapper.chmod(0o755)
            legacy = dict(env, OBJDUMP=str(wrapper))
            self.assertEqual(generated(legacy), 8)
            subprocess.run(
                ["patch", "--batch", "-p1", "-i", str(ROOT / "cfi-bti-parser.patch")],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
            )
            self.assertEqual(generated(env), 8)
            self.assertEqual(generated(legacy), 8)
            # Synthesize ONLY test symbols as a linker would, leaving code/relocations
            # intact. These fixtures are not camera modules or installable outputs.
            parsed = Elf(obj)
            table = parsed.ss[parsed.names.index(".symtab")]
            sym_index = next(
                i for i, s in enumerate(parsed.sy) if s["name"] == "callback.cfi_jt"
            )
            for label, address in [("bad", 12), ("correct", 8)]:
                data = bytearray(parsed.d)
                offset = table[4] + sym_index * 24
                struct.pack_into("<H", data, offset + 6, parsed.names.index(".text"))
                struct.pack_into("<Q", data, offset + 8, address)
                path = root / (label + ".o")
                path.write_bytes(data)
                inventory = pointer_inventory(path)
                self.assertEqual(inventory["published_pointer_count"], 1)
                self.assertEqual(
                    len(inventory["invalid_published_pointers"]), int(label == "bad")
                )
            damaged = bytearray(data)
            struct.pack_into(
                "<I",
                damaged,
                parsed.ss[parsed.names.index(".text")][4] + 12,
                0xD503201F,
            )
            broken = root / "nonbranch.o"
            broken.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, "not on a B instruction"):
                pointer_inventory(broken)


if __name__ == "__main__":
    unittest.main()
