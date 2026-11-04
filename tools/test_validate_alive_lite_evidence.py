#!/usr/bin/env python3
"""Negative-control tests for the independent Alive-lite JSONL accounting gate."""

import copy
import unittest

from validate_alive_lite_evidence import EvidenceError, audit

SHA = "a" * 40
PASSES = ("PRE", "SCCP", "CSE", "pipeline")
MEMORY_PASSES = ("forward", "dse", "pipeline")


def valid_evidence():
    coverage = []
    proofs = []
    for shape, seed in [(n, 0) for n in range(6)] + [(0, 1), (1, 1)]:
        for name in PASSES:
            coverage.append(dict(shape=shape, seed=seed, changed=True,
                                 concrete_tested=True, z3_proven=False,
                                 status="CONCRETE_ONLY", effect_count=1, behavior_count=0,
                                 **{"pass": name}))
    for shape in range(6):
        for name in PASSES:
            proofs.append(dict(shape=shape, seed=0, pass_name=name, changed=True,
                               status="PROVEN", source_sha=SHA))
    bounded = [dict(scope="bounded_local_memory", fixture=fixture, pass_name=name,
                    cases=7, status="BOUNDED_ONLY", smt_proven=False)
               for fixture in ("pointer_phi", "overlap_zero", "loop_memory")
               for name in MEMORY_PASSES]
    bounded += [dict(family="family", seed=i, **{"pass": name}, cases=7,
                     changed=True, status="BOUNDED_ONLY", smt_proven=False)
                for i in range(31) for name in MEMORY_PASSES]
    memory_proofs = [dict(scope="exact_local_i32_words", fixture=fixture,
                          pass_name=name, source_sha=SHA, changed=True, status="PROVEN",
                          smt_proven=True)
                     for fixture in ("overwrite", "forward_join", "pointer_phi", "bulk_zero")
                     for name in MEMORY_PASSES]
    return (coverage, proofs, bounded, memory_proofs)


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = list(valid_evidence())

    def check_failure(self, phrase):
        with self.assertRaisesRegex(EvidenceError, phrase):
            audit(*self.rows, SHA)

    def test_valid_complete_matrix(self):
        result = audit(*self.rows, SHA)
        self.assertEqual(result["scalar_proven_changed"], 24)
        self.assertEqual(result["bounded_memory_records"], 102)

    def test_reject_missing_scalar_pass(self):
        self.rows[1].pop()
        self.check_failure("incomplete pass matrix")

    def test_reject_duplicate_scalar_proof(self):
        self.rows[1].append(copy.deepcopy(self.rows[1][0]))
        self.check_failure("duplicate")

    def test_reject_claimed_concrete_proof(self):
        self.rows[0][0]["z3_proven"] = True
        self.check_failure("falsely claims SMT")

    def test_reject_inconsistent_observed_change(self):
        self.rows[0][0]["effect_count"] = 0
        self.check_failure("changed disagrees")

    def test_reject_stale_scalar_proof(self):
        self.rows[1][0]["source_sha"] = "b" * 40
        self.check_failure("source revision mismatch")

    def test_reject_bounded_memory_marked_proven(self):
        self.rows[2][0]["status"] = "PROVEN"
        self.check_failure("overstated")

    def test_reject_duplicate_generated_memory_seed(self):
        self.rows[2][-1]["seed"] = 0
        self.check_failure("duplicate")

    def test_reject_missing_exact_word_fixture_pass(self):
        self.rows[3].pop()
        self.check_failure("expected 12")

    def test_reject_proof_without_changed_transformation(self):
        self.rows[3][0]["changed"] = False
        self.check_failure("verdict conflicts")

    def test_reject_memory_smt_flag_mismatch(self):
        self.rows[3][0]["smt_proven"] = False
        self.check_failure("SMT proof flag")

    def test_reject_invalid_source_sha(self):
        with self.assertRaisesRegex(EvidenceError, "invalid source SHA"):
            audit(*self.rows, "not-a-sha")


if __name__ == "__main__":
    unittest.main()
