#!/usr/bin/env python3
"""Fail-closed consistency audit for T5 Alive-lite CI evidence artifacts.

This validates accounting, not solver correctness. It keeps concrete,
bounded memory, scalar SMT, and exact-word SMT verdicts distinct.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PASSES = frozenset(("PRE", "SCCP", "CSE", "pipeline"))
MEMORY_PASSES = frozenset(("forward", "dse", "pipeline"))
MEMORY_FIXTURES = frozenset(("pointer_phi", "overlap_zero", "loop_memory"))
SMT_FIXTURES = frozenset(("overwrite", "forward_join", "pointer_phi", "bulk_zero"))


class EvidenceError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise EvidenceError(message)


def integer(value):
    return type(value) is int and value >= 0


def boolean(value):
    return type(value) is bool


def load_jsonl(path):
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        require(bool(line.strip()), f"{path}:{number}: blank JSONL record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{path}:{number}: malformed JSON: {exc}") from exc
        require(type(row) is dict, f"{path}:{number}: expected JSON object")
        records.append(row)
    require(bool(records), f"{path}: no evidence records")
    return records


def check_matrix(rows, *, passes, key_field="pass", id_fields=("shape", "seed"),
                 expected_groups=None):
    """Require each independent fixture/seed to have exactly one row per pass."""
    by_group = defaultdict(set)
    for row in rows:
        key = tuple(row.get(field) for field in id_fields)
        require(None not in key, f"missing identity field in {row}")
        name = row.get(key_field)
        require(name in passes, f"unknown {key_field} {name!r} in {key}")
        require(name not in by_group[key], f"duplicate {key_field} {name} in {key}")
        by_group[key].add(name)
    for key, observed in by_group.items():
        require(observed == passes, f"incomplete pass matrix {key}: {sorted(passes - observed)}")
    if expected_groups is not None:
        require(set(by_group) == expected_groups, "missing or unexpected fixture matrix group")
    return by_group


def check_scalar_coverage(rows):
    require(len(rows) == 32, f"expected 32 concrete T5 records, got {len(rows)}")
    groups = check_matrix(rows, passes=PASSES)
    require(len(groups) == 8, "concrete T5 campaign must have 8 selected cases")
    require({key[0] for key in groups} == set(range(6)),
            "concrete T5 selection must cover all 6 CFG shapes")
    changed = Counter()
    for row in rows:
        require(integer(row.get("shape")) and row["shape"] < 6 and integer(row.get("seed")),
                "invalid concrete shape/seed")
        require(boolean(row.get("changed")) and row.get("concrete_tested") is True,
                "concrete record missing actual comparison")
        require(row.get("z3_proven") is False, "concrete evidence falsely claims SMT proof")
        require(integer(row.get("effect_count")) and integer(row.get("behavior_count")),
                "missing structural/behavior counters")
        actual = row["effect_count"] + row["behavior_count"] > 0
        require(row["changed"] == actual, "concrete changed disagrees with measured effects")
        require(row.get("status") == ("CONCRETE_ONLY" if actual else "NOT_EXERCISED"),
                "concrete verdict conflicts with changed bit")
        if actual:
            changed[row["pass"]] += 1
    require(all(changed[p] > 0 for p in PASSES), "some optimizer pass has no changed concrete case")
    return sum(changed.values())


def check_scalar_proofs(rows, source_sha):
    groups = check_matrix(rows, passes=PASSES, key_field="pass_name")
    require(6 <= len(groups) <= 10, "unexpected T5 Z3 selected-case count")
    require({key[0] for key in groups} == set(range(6)), "missing Z3 proof CFG shapes")
    changed = Counter()
    for row in rows:
        require(integer(row.get("shape")) and row["shape"] < 6 and integer(row.get("seed")),
                "invalid Z3 proof shape/seed")
        require(row.get("source_sha") == source_sha, "scalar proof source revision mismatch")
        require(boolean(row.get("changed")), "missing scalar changed bit")
        require(row.get("status") == ("PROVEN" if row["changed"] else "NOT_EXERCISED"),
                "scalar proof verdict conflicts with changed bit")
        if row["changed"]:
            changed[row["pass_name"]] += 1
    require(all(changed[p] > 0 for p in PASSES), "some optimizer pass lacks changed Z3 proof")
    return sum(changed.values())


def check_bounded_memory(rows):
    fixed = [r for r in rows if r.get("scope") == "bounded_local_memory"]
    generated = [r for r in rows if r.get("scope") != "bounded_local_memory"]
    require(len(fixed) == 9 and len(generated) == 93,
            f"wrong bounded-memory counts: {len(fixed)} fixed, {len(generated)} generated")
    check_matrix(fixed, passes=MEMORY_PASSES, key_field="pass_name",
                 id_fields=("fixture",),
                 expected_groups={(f,) for f in MEMORY_FIXTURES})
    for row in fixed:
        require(integer(row.get("cases")) and row["cases"] > 0,
                "bounded fixture has no concrete inputs")
    check_matrix(generated, passes=MEMORY_PASSES,
                 id_fields=("family", "seed"))
    require(len({(r["family"], r["seed"]) for r in generated}) == 31,
            "expected 31 generated memory inputs")
    require(all(boolean(r.get("changed")) for r in generated),
            "generated memory rows lack changed bits")
    require(any(r["changed"] for r in generated), "memory oracle exercised no real change")
    for row in generated:
        require(isinstance(row.get("family"), str) and bool(row["family"])
                and integer(row.get("seed")) and row.get("cases") == 7,
                "invalid generated memory case")
    for row in rows:
        require(row.get("status") == "BOUNDED_ONLY" and row.get("smt_proven") is False,
                "bounded memory evidence overstated as a formal proof")
    return len(rows)


def check_memory_proofs(rows, source_sha):
    require(len(rows) == 12, f"expected 12 exact-word SMT records, got {len(rows)}")
    check_matrix(rows, passes=MEMORY_PASSES, key_field="pass_name",
                 id_fields=("fixture",),
                 expected_groups={(f,) for f in SMT_FIXTURES})
    proven = 0
    for row in rows:
        require(row.get("scope") == "exact_local_i32_words", "wrong exact-word proof scope")
        require(row.get("source_sha") == source_sha, "memory proof source revision mismatch")
        require(boolean(row.get("changed")), "missing memory proof changed bit")
        require(row.get("status") == ("PROVEN" if row["changed"] else "NOT_EXERCISED"),
                "exact-word proof verdict conflicts with changed bit")
        require(row.get("smt_proven") is row["changed"],
                "exact-word SMT proof flag conflicts with verdict")
        proven += int(row["changed"])
    require(proven > 0, "zero changed memory optimizations proved")
    return proven


def audit(coverage, proofs, bounded, memory_proofs, source_sha):
    require(bool(re.fullmatch(r"[0-9a-f]{40}", source_sha)), "invalid source SHA")
    return {
        "concrete_changed": check_scalar_coverage(coverage),
        "scalar_proven_changed": check_scalar_proofs(proofs, source_sha),
        "bounded_memory_records": check_bounded_memory(bounded),
        "memory_proven_changed": check_memory_proofs(memory_proofs, source_sha),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--proofs", required=True)
    parser.add_argument("--bounded-memory", required=True)
    parser.add_argument("--memory-proofs", required=True)
    args = parser.parse_args()
    try:
        result = audit(load_jsonl(args.coverage), load_jsonl(args.proofs),
                       load_jsonl(args.bounded_memory), load_jsonl(args.memory_proofs),
                       args.source_sha)
    except (EvidenceError, OSError) as exc:
        parser.exit(1, f"Alive-lite evidence audit FAILED: {exc}\n")
    print("Alive-lite evidence audit PASS:", json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
