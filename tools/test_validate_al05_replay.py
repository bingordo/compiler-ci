"""Negative controls for AL-05 replay provenance, seed and envelope validation."""
import tempfile
import unittest
from pathlib import Path

from validate_al05_replay import validate, verify_file

SHA = "1a" * 20


def replay(seed: int) -> str:
    """Construct one minimal syntactically shaped provenance envelope."""
    original = [
        "compiler-ir-native-module 3",
        "functions 1",
        f"function al05_inductive_{seed}",
        "endfunction",
        "endmodule",
    ]
    changed = [
        "compiler-ir-native-module 3",
        "functions 1",
        f"function al05_inductive_{seed}",
        "optimized_cse_marker",
        "endfunction",
        "endmodule",
    ]
    return "\n".join([
        "AL05_INDUCTION_REPLAY 1",
        "contract al05-closed-i32-cse-loop-v1",
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
        for i in range(4):
            (self.root / f"al05-induction-{i}.replay").write_text(replay(i))

    def test_accepts_complete_four_seed_envelope(self) -> None:
        self.assertEqual(validate(self.root, SHA), 4)

    def test_rejects_missing_and_extra_seed(self) -> None:
        (self.root / "al05-induction-3.replay").unlink()
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


if __name__ == "__main__":
    unittest.main()
