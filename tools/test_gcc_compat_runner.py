"""Native positive and negative controls for compatibility result accounting."""
from pathlib import Path
import shutil
import tempfile
import unittest

from gcc_compat_inventory import inventory
from gcc_compat_runner import fatal, run_case, unsupported, missing_required


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

    def test_required_directions_cannot_disappear(self):
        record = run_case(self.case(), self.root, self.root / "required",
                          "gcc", self.flags,
                          {"control": ["gcc", "-c", "{source}", "-o", "{object}"]})
        requirements = [{"frontend": "control", "cases": ["struct-return-0"],
                         "options": ["-O0"], "directions": ["x", "y"]}]
        self.assertEqual(missing_required([record], requirements, ["control"]), [])
        record["frontends"]["control"]["directions"]["x"]["status"] = "NOT_ADMITTED"
        self.assertEqual(missing_required([record], requirements, ["control"]),
                         [("control", "struct-return-0", "-O0", "x")])
        self.assertEqual(len(missing_required([], requirements, ["control"])), 2)
        self.assertEqual(missing_required([], requirements, ["other"]), [])

    def test_mismatch_is_fatal(self):
        record = run_case(self.case(), self.root, self.root / "bad",
                          "gcc", self.flags,
                          {"bad": ["gcc", "-DVALUE=12", "-c", "{original}", "-o", "{object}"]})
        self.assertEqual(record["frontends"]["bad"]["directions"]["y"]["status"],
                         "ABI_MISMATCH")
        self.assertTrue(fatal(record))

    def test_generated_return_controls(self):
        from gcc_compat_return_probes import generate
        root = self.root / "generated"
        generate(root)
        cases = inventory(root, "probe-return-")
        self.assertEqual(len(cases), 45)
        for name in ("bytes-3", "bytes-9", "bytes-17", "gp-sse", "short-checks-33"):
            case = next(c for c in cases if c["case"] == "probe-return-" + name)
            record = run_case(case, root, self.root / name, "gcc", self.flags,
                              {"control": ["gcc", "-c", "{original}", "-o", "{object}"]})
            self.assertFalse(fatal(record), record)
            self.assertTrue(all(d["status"] == "PASS"
                                for d in record["frontends"]["control"]["directions"].values()))

    def test_target_only_skip_selector(self):
        path = self.root / "struct-return-0_main.c"
        path.write_text('/* { dg-skip-if "limited code space" { pdp11-*-* } } */\n'
                        + path.read_text())
        case = self.case()
        self.assertIsNone(unsupported(case, self.root, "x86_64-linux-gnu"))
        self.assertIn("target exclusion", unsupported(case, self.root, "pdp11-unknown-none"))

    def test_layout_directive_scope_and_selection(self):
        from gcc_compat_bitfield_layout import layout_directive, select_batches
        target = "x86_64-linux-gnu"
        root = self.root
        line = '/* { dg-options "-w -I' + str(root) + ' -mno-mmx -Wno-abi" { target i?86-*-* x86_64-*-* } } */'
        self.assertTrue(layout_directive(line, root, target))
        self.assertFalse(layout_directive(line, root, "aarch64-linux-gnu"))
        self.assertFalse(layout_directive(line.replace("-mno-mmx", "-fpack-struct"), root, target))
        self.assertFalse(layout_directive('/* { dg-require-effective-target unknown } */', root, target))
        names = ["t001", "t002", "t003", "t004", "t005"]
        self.assertEqual(select_batches(names, 0), names)
        self.assertEqual(select_batches(names, 3), ["t001", "t003", "t005"])
        self.assertEqual(select_batches(names, 1), ["t001"])
        with self.assertRaises(ValueError):
            select_batches(names, -1)

    def test_bitfield_interoperability_and_layout_mismatch(self):
        # A separate ABI oracle tests the runner's ability to detect
        # bitfield layout corruption, rather than merely matching source.
        root = self.root
        (root / "bitfield_main.c").write_text(
            "int check(void); int main(void) { return check(); }\n")
        (root / "bitfield.h").write_text(
            "struct B { unsigned a:3; signed b:5; unsigned :0; unsigned c:9; };\n"
            "struct B make(void);\n")
        (root / "bitfield_x.c").write_text(
            '#include "bitfield.h"\nint check(void) { struct B b=make(); '
            "return b.a!=5 || b.b!=-7 || b.c!=273; }\n")
        (root / "bitfield_y.c").write_text(
            '#include "bitfield.h"\nstruct B make(void) { struct B b; '
            "b.a=5; b.b=-7; b.c=273; return b; }\n")
        case = inventory(root, "bitfield")[0]
        record = run_case(case, root, root / "bitfield-work", "gcc", self.flags,
                          {"control": ["gcc", "-c", "{original}", "-o", "{object}"]})
        self.assertFalse(fatal(record), record)
        self.assertTrue(all(d["status"] == "PASS"
                            for d in record["frontends"]["control"]["directions"].values()))

    def test_unknown_directive_is_not_executed(self):
        path = self.root / "struct-return-0_main.c"
        path.write_text("/* { dg-require-effective-target unknown_feature } */\n"
                        + path.read_text())
        self.assertIn("unhandled directive", unsupported(self.case(), self.root))


if __name__ == "__main__":
    unittest.main()
