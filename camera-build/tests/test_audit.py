"""Independent LLVM assembler/objcopy fixtures for the runtime relocation rule."""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from audit import audit, cfi_symbol_surface


class CFISurfaceTests(unittest.TestCase):
    def row(self, name, binding=0, visibility=0, defined=True, kind=2, size=0):
        return {'name': name, 'binding': binding, 'visibility': visibility, 'defined': defined, 'type': kind, 'size': size}

    def test_internal_linker_spelling_and_mapping_symbols(self):
        original = [self.row('callback.5613.cfi_jt'), self.row('$x.cfi_jt', binding=1, kind=0)]
        baseline = [self.row('callback.' + 'a'*32 + '.cfi_jt', binding=1, visibility=2, kind=0)]
        self.assertEqual(cfi_symbol_surface(original), cfi_symbol_surface(baseline))

    def test_public_or_undefined_names_never_normalize(self):
        for arguments in [{'binding': 1}, {'defined': False}]:
            self.assertNotEqual(cfi_symbol_surface([self.row('callback.1.cfi_jt', **arguments)]),
                                cfi_symbol_surface([self.row('callback.2.cfi_jt', **arguments)]))

    def test_missing_duplicate_or_changed_target_is_rejected(self):
        original = [self.row('callback.1.cfi_jt'), self.row('callback.2.cfi_jt')]
        self.assertNotEqual(cfi_symbol_surface(original), cfi_symbol_surface(original[:1]))
        self.assertNotEqual(cfi_symbol_surface(original), cfi_symbol_surface([self.row('other.cfi_jt')]*2))

    def test_mapping_spelling_does_not_hide_functions(self):
        self.assertEqual(cfi_symbol_surface([self.row('$x.cfi_jt')]), ['$x.cfi_jt'])
        self.assertEqual(cfi_symbol_surface([self.row('$x.cfi_jt', kind=0, size=8)]), ['$x.cfi_jt'])


@unittest.skipUnless(
    shutil.which("llvm-mc") and shutil.which("llvm-objcopy"),
    "LLVM inspection tools required",
)
class RelocationTests(unittest.TestCase):
    def fixture(self, root, name, branch="bl", allocated_debug=False):
        source = root / (name + ".s")
        source.write_text(
            ".text\n.globl function\n.type function,@function\nfunction:\n"
            f"{branch} external\nret\n.size function,.-function\n"
            '.section .debug_info,"",@progbits\n.quad function\n'
        )
        obj = root / (name + ".o")
        subprocess.run(
            [
                "llvm-mc",
                "-triple=aarch64-linux-gnu",
                "-filetype=obj",
                str(source),
                "-o",
                str(obj),
            ],
            check=True,
        )
        if allocated_debug:
            subprocess.run(
                [
                    "llvm-objcopy",
                    "--set-section-flags",
                    ".debug_info=alloc,readonly",
                    str(obj),
                ],
                check=True,
            )
        return obj

    def test_strip_debug_preserves_runtime_properties_and_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            original = self.fixture(root, "original")
            stripped = root / "stripped.o"
            subprocess.run(
                ["llvm-objcopy", "--strip-debug", str(original), str(stripped)],
                check=True,
            )
            left, right = audit(original), audit(stripped)
            self.assertNotEqual(
                left["relocation_type_counts"], right["relocation_type_counts"]
            )
            self.assertEqual(
                left["runtime_relocation_type_counts"],
                right["runtime_relocation_type_counts"],
            )
            self.assertEqual(
                left["function_fingerprints"], right["function_fingerprints"]
            )

    def test_runtime_branch_relocation_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            left = audit(self.fixture(root, "call", "bl"))
            right = audit(self.fixture(root, "jump", "b"))
            self.assertNotEqual(
                left["runtime_relocation_type_counts"],
                right["runtime_relocation_type_counts"],
            )

    def test_allocated_debug_named_section_is_never_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            value = audit(self.fixture(root, "allocated", allocated_debug=True))
            self.assertIn(".rela.debug_info", value["runtime_relocation_type_counts"])


if __name__ == "__main__":
    unittest.main()
