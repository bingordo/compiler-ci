#!/usr/bin/env python3
"""CLI regression tests for the mrustc-generated C feature inventory."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("probe-mrustc-c-dialect.py")


class GeneratedCDialectInventoryTests(unittest.TestCase):
    def test_atomic_qualifiers_apis_and_builtin_families_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ir = directory / "libcompiler_ir-0_1_0.rlib.c"
            ir.write_text(
                "_Atomic int counter;\n"
                "int f(void) { return atomic_load_explicit(&counter, 5); }\n"
                "int g(void) { return __atomic_fetch_add(&counter, 1, 0); }\n"
            )
            x86 = directory / "libcompiler_backend_x86-0_1_0.rlib.c"
            x86.write_text("int x86_answer(void) { return 42; }\n")
            # Different compiler families are always selected first. Tiny
            # atomic-free units then supplement (not replace) those probes.
            helpers = [directory / f"helper_{i}.c" for i in range(3)]
            for i, helper in enumerate(helpers):
                helper.write_text(f"int helper_{i}(void) {{ return {i}; }}\n")
            selection = directory / "selection.txt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(directory), str(selection)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("C_DIALECT generated_units=5", proc.stdout)
            self.assertIn("C_DIALECT atomic_free_extra_samples=2", proc.stdout)
            self.assertIn("C_DIALECT C11 _Atomic keyword: files=1 matches=1", proc.stdout)
            self.assertIn(
                "C_DIALECT C11 atomic API identifiers: files=1 matches=1",
                proc.stdout,
            )
            self.assertIn(
                "C_DIALECT GNU atomic builtin identifiers: files=1 matches=1",
                proc.stdout,
            )
            self.assertIn(
                "C_SAMPLE_ATOMIC_HINT name=libcompiler_ir-0_1_0.rlib.c "
                "atomic_qualifier=1 atomic_api=1 gnu_builtin=1 "
                "native_oracle=gcc",
                proc.stdout,
            )
            self.assertIn(
                "C_SAMPLE_ATOMIC_HINT name=libcompiler_backend_x86-0_1_0.rlib.c "
                "atomic_qualifier=0 atomic_api=0 gnu_builtin=0 "
                "native_oracle=gcc",
                proc.stdout,
            )
            selected = selection.read_text().splitlines()
            self.assertEqual(len(selected), 4)
            self.assertIn(str(ir), selected)
            self.assertIn(str(x86), selected)
            self.assertIn(str(helpers[0]), selected)
            self.assertIn(str(helpers[1]), selected)
            self.assertNotIn(str(helpers[2]), selected)

    def test_no_generated_c_is_a_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            selection = directory / "selection.txt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(directory), str(selection)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("No generated C files", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
