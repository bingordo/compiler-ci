"""Negative controls for content-addressed AL-05 scalar/CFG scope evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from al05_semantic_manifest import (
    SCOPE_BYTES, build, canonical, verify_manifest, read_scope,
)
from test_validate_al05_replay import SHA, replay


class ScopeEvidenceTests(unittest.TestCase):
    """No accidental v1/v2 mixture or silent semantic-contract reassignment."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.records = self.root / "replays"
        self.records.mkdir()
        self.scope = self.root / "al05_semantic_scope_v1.txt"
        self.scope.write_bytes(SCOPE_BYTES)
        self.manifest = self.root / "scope.json"
        for family in ("induction", "affine"):
            for seed in range(4):
                (self.records / f"al05-{family}-{seed}.replay").write_text(
                    replay(seed, family)
                )
        self.manifest.write_bytes(canonical(build(self.records, SHA, self.scope)))

    def test_all_eight_records_bound_with_fixed_scope(self) -> None:
        record = verify_manifest(self.manifest, self.records, SHA, self.scope)
        self.assertEqual(len(record["snapshots"]), 8)
        self.assertIs(record["normative_fs1"], False)
        self.assertEqual(record["compiler_sha"], SHA)

    def test_reject_replayed_record_mutation(self) -> None:
        changed = self.records / "al05-affine-0.replay"
        changed.write_text(replay(0, "affine").replace(
            "optimized_cse_marker", "another_cse_marker"))
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, self.records, SHA, self.scope)

    def test_reject_replay_with_other_version(self) -> None:
        changed = self.records / "al05-affine-1.replay"
        changed.write_text(replay(1, "induction"))
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, self.records, SHA, self.scope)

    def test_missing_replay_cannot_be_skipped(self) -> None:
        (self.records / "al05-induction-3.replay").unlink()
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, self.records, SHA, self.scope)

    def test_reject_changed_or_extra_semantics(self) -> None:
        for bad in [
            SCOPE_BYTES.replace(b"add=modular-bvadd", b"add=saturating"),
            SCOPE_BYTES.replace(b"status=test-local-not-normative-FS1",
                                b"status=normative-FS1"),
            SCOPE_BYTES + b"memory=something-else\n",
            SCOPE_BYTES.replace(b"block-args=parallel", b"block-args=sequential"),
            SCOPE_BYTES[:-1],
        ]:
            self.scope.write_bytes(bad)
            with self.assertRaises(ValueError):
                read_scope(self.scope)
            with self.assertRaises(ValueError):
                verify_manifest(self.manifest, self.records, SHA, self.scope)
        self.scope.write_bytes(SCOPE_BYTES)

    def test_reject_forged_normative_claim_or_hash(self) -> None:
        original = json.loads(self.manifest.read_text())
        for property_name, value in [
            ("normative_fs1", True),
            ("scope_sha256", "0" * 64),
            ("compiler_sha", "f" * 40),
            ("ir", "compiler-ir-native-module-4"),
            ("unrecognized", "surprise"),
        ]:
            bad = dict(original)
            bad[property_name] = value
            self.manifest.write_bytes(canonical(bad))
            with self.assertRaises(ValueError):
                verify_manifest(self.manifest, self.records, SHA, self.scope)
        self.manifest.write_bytes(canonical(original))

    def test_reject_noncanonical_or_trailing_evidence(self) -> None:
        for bad in [
            self.manifest.read_bytes().replace(b"\n", b"\r\n"),
            self.manifest.read_bytes() + b"extra\n",
            b"null\n",
            b"{}\n",
            b'{"format":"broken"}\n',
        ]:
            self.manifest.write_bytes(bad)
            with self.assertRaises(ValueError):
                verify_manifest(self.manifest, self.records, SHA, self.scope)

    def test_reject_wrong_sha_or_wrong_semantic_file(self) -> None:
        for bad in ["local", "deadbeef", "F" * 40, "ab" * 20]:
            with self.assertRaises(ValueError):
                verify_manifest(self.manifest, self.records, bad, self.scope)
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, self.records, SHA,
                            self.root / "no-semantic-scope.txt")

    def test_reject_forged_snapshot_hash(self) -> None:
        content = json.loads(self.manifest.read_text())
        content["snapshots"]["al05-affine-2.replay"]["sha256"] = "0" * 64
        self.manifest.write_bytes(canonical(content))
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, self.records, SHA, self.scope)


if __name__ == "__main__":
    unittest.main()
