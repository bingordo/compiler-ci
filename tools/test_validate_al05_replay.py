"""Negative controls for AL-05 replay provenance, seed and envelope validation."""
import tempfile
import unittest
from pathlib import Path

from validate_al05_replay import validate, verify_file

SHA = "1a" * 20


def replay(seed: int, family: str = "induction") -> str:
    """Construct syntactically shaped *but separate* versioned envelopes."""
    if family not in ("induction", "affine"):
        raise ValueError("unknown fixture law")
    prefix = "al05_inductive" if family == "induction" else "al05_affine"
    magic = "AL05_INDUCTION_REPLAY 1" if family == "induction" else "AL05_INDUCTION_REPLAY 2"
    contract = ("al05-closed-i32-cse-loop-v1" if family == "induction"
                else "al05-closed-i32-affine-cse-loop-v2")
    original = [
        "compiler-ir-native-module 3",
        "functions 1",
        f"function {prefix}_{seed}",
        "endfunction",
        "endmodule",
    ]
    changed = [
        "compiler-ir-native-module 3",
        "functions 1",
        f"function {prefix}_{seed}",
        "optimized_cse_marker",
        "endfunction",
        "endmodule",
    ]
    return "\n".join([
        magic,
        f"contract {contract}",
        "ir compiler-ir-native-module-3",
        "pass typed-cse",
        f"seed {seed}",
        f"compiler_sha {SHA}",
        f"before_lines {len(original)}",
        *original,
        f"after_lines {len(changed)}",
        *changed,
        "endreplay", "",
    ])


class ReplayEnvelopeTests(unittest.TestCase):
    """Keep corruption and source-substitution checks fail-closed."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for family in ("induction", "affine"):
            for i in range(4):
                (self.root / f"al05-{family}-{i}.replay").write_text(replay(i, family))

    def test_accepts_complete_eight_seed_envelopes(self) -> None:
        self.assertEqual(validate(self.root, SHA), 8)

    def test_rejects_missing_and_extra_seed(self) -> None:
        (self.root / "al05-affine-3.replay").unlink()
        with self.assertRaises(ValueError):
            validate(self.root, SHA)
        (self.root / "surplus.txt").write_text("x")
        with self.assertRaises(ValueError):
            validate(self.root, SHA)

    def test_rejects_unpinned_or_mismatched_sha(self) -> None:
        for bad in ["local", "deadbeef", "A" * 40, "2a" * 20]:
            with self.assertRaises(ValueError):
                validate(self.root, bad)

    def test_rejects_invalid_contract_format_and_pass(self) -> None:
        for a, b in [
            ("contract al05-closed-i32-cse-loop-v1", "contract fake"),
            ("ir compiler-ir-native-module-3", "ir compiler-ir-native-module-4"),
            ("pass typed-cse", "pass fake"),
        ]:
            path = self.root / "al05-induction-0.replay"
            path.write_text(replay(0).replace(a, b))
            with self.assertRaises(ValueError):
                verify_file(path, 0, SHA)

    def test_rejects_corrupt_lengths_or_extra_data(self) -> None:
        path = self.root / "al05-induction-0.replay"
        for body in [
            replay(0).replace("before_lines 5", "before_lines 5000"),
            replay(0).replace("before_lines 5", "before_lines 05"),
            replay(0) + "trailer\n",
            replay(0)[:-1],
        ]:
            path.write_text(body)
            with self.assertRaises(ValueError):
                verify_file(path, 0, SHA)

    def test_rejects_reused_source_or_bad_function_identity(self) -> None:
        path = self.root / "al05-induction-0.replay"
        body = replay(0)
        body = body.replace("after_lines 6", "after_lines 5").replace(
            "optimized_cse_marker\n", ""
        )
        path.write_text(body)
        with self.assertRaises(ValueError):
            verify_file(path, 0, SHA)
        path.write_text(replay(0).replace("function al05_inductive_0",
                                          "function al05_inductive_3"))
        with self.assertRaises(ValueError):
            verify_file(path, 0, SHA)

    def test_rejects_bad_bytes_and_wrong_filename_seed(self) -> None:
        path = self.root / "al05-induction-0.replay"
        path.write_bytes(replay(0).encode("utf-8") + b"\x00")
        with self.assertRaises(ValueError):
            verify_file(path, 0, SHA)
        path.write_text(replay(0))
        with self.assertRaises(ValueError):
            verify_file(path, 3, SHA)


    def test_affine_rejects_old_contract_and_swapped_family(self) -> None:
        path = self.root / "al05-affine-0.replay"
        path.write_text(replay(0, "affine").replace(
            "contract al05-closed-i32-affine-cse-loop-v2",
            "contract al05-closed-i32-cse-loop-v1"))
        with self.assertRaises(ValueError):
            verify_file(path, 0, SHA, "affine")
        path.write_text(replay(0, "induction"))
        with self.assertRaises(ValueError):
            verify_file(path, 0, SHA, "affine")

    def test_affine_wrong_typeid_function_and_sha(self) -> None:
        path = self.root / "al05-affine-0.replay"
        for bad in [
            replay(0, "affine").replace("function al05_affine_0",
                                         "function al05_inductive_0"),
            replay(0, "affine").replace("compiler_sha " + SHA,
                                         "compiler_sha " + "a1" * 20),
            replay(0, "affine").replace("AL05_INDUCTION_REPLAY 2",
                                         "AL05_INDUCTION_REPLAY 3"),
            replay(0, "affine") + "unexpected\n",
        ]:
            path.write_text(bad)
            with self.assertRaises(ValueError):
                verify_file(path, 0, SHA, "affine")

    def test_affine_rejects_non_cse_and_bad_module_boundaries(self) -> None:
        path = self.root / "al05-affine-0.replay"
        identical = replay(0, "affine").replace(
            "after_lines 6", "after_lines 5").replace(
            "optimized_cse_marker\n", "")
        for bad in [
            identical,
            replay(0, "affine").replace("before_lines 5", "before_lines 06"),
            replay(0, "affine").replace("endmodule", "bogusmodule"),
        ]:
            path.write_text(bad)
            with self.assertRaises(ValueError):
                verify_file(path, 0, SHA, "affine")


if __name__ == "__main__":
    unittest.main()
