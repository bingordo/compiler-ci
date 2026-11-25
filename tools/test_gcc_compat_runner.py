"""Native positive and negative controls for compatibility result accounting."""
from pathlib import Path
import shutil
import tempfile
import unittest

from gcc_compat_inventory import inventory
from gcc_compat_runner import fatal, run_case, unsupported


@unittest.skipUnless(shutil.which("gcc"), "native GCC required")
class CompatibilityTests(unittest.TestCase):
    """A small cross-object return case checks all assignments, not just main."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "struct-return-0.h").write_text(
            "struct pair { long a,b; }; struct pair ret(void);\n")
        (self.root / "struct-return-0_main.c").write_text(
            "void check(void); int main(void) { check(); return 0; }\n")
        (self.root / "struct-return-0_x.c").write_text(
            '#include "struct-return-0.h"\n'
            "void check(void) { struct pair r=ret(); "
            "if(r.a!=7 || r.b!=11) __builtin_abort(); }\n")
        (self.root / "struct-return-0_y.c").write_text(
            '#include "struct-return-0.h"\n'
            "#ifndef VALUE\n#define VALUE 11\n#endif\n"
            "struct pair ret(void) { struct pair r={7,VALUE}; return r; }\n")
        self.flags = ["-std=gnu11", "-O0", "-fno-pie"]

    def case(self):
        return inventory(self.root, "struct-return-")[0]

    def test_all_assignments(self):
        record = run_case(self.case(), self.root, self.root / "work",
                          "gcc", self.flags,
                          {"control": ["gcc", "-c", "{source}", "-o", "{object}"]})
        self.assertEqual(record["status"], "REFERENCE_PASS")
        directions = record["frontends"]["control"]["directions"]
        self.assertEqual(set(directions), {"main", "x", "y", "main+x", "main+y", "x+y"})
        self.assertTrue(all(d["status"] == "PASS" for d in directions.values()))
        self.assertFalse(fatal(record))

    def test_mismatch_is_fatal(self):
        record = run_case(self.case(), self.root, self.root / "bad",
                          "gcc", self.flags,
                          {"bad": ["gcc", "-DVALUE=12", "-c", "{original}", "-o", "{object}"]})
        self.assertEqual(record["frontends"]["bad"]["directions"]["y"]["status"],
                         "ABI_MISMATCH")
        self.assertTrue(fatal(record))

    def test_unknown_directive_is_not_executed(self):
        path = self.root / "struct-return-0_main.c"
        path.write_text("/* { dg-require-effective-target unknown_feature } */\n"
                        + path.read_text())
        self.assertIn("unhandled directive", unsupported(self.case(), self.root))


if __name__ == "__main__":
    unittest.main()
