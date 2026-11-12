#!/usr/bin/env python3
"""CT11: exact-source PRE census over complete, unchanged upstream C TUs.

Consumes the original 27-TU S23.39 census (all attempted, no source
rewrites), retaining every rejected source and error. This is diagnostic
evidence only, not an optimization or an LLVM capability claim.
"""
import argparse
import hashlib
import json
from pathlib import Path

EXPECTED_UPSTREAM = "7813e9119eb5fe681b6750b8a033a0490bca0941"
TIME_FIELDS = (
    "input_validation", "module_clone", "function_clone", "analysis",
    "trial_clone", "trial_validation", "final_validation", "commit",
)
COUNT_FIELDS = (
    "functions", "analysis_rounds", "candidates_examined", "trials", "edits",
    "rejected_trials", "budget_skips", "no_edit_functions",
)

def analyze(path: Path, output: Path) -> dict:
    record = json.loads(path.read_text())
    if record["upstream_commit"] != EXPECTED_UPSTREAM:
        raise RuntimeError("wrong original musl source revision")
    if record["attempted"] != 27 or len(record["cases"]) != 27:
        raise RuntimeError("original 27-TU census incomplete")
    if record["accepted"] + record["rejected"] != 27:
        raise RuntimeError("one or more source successes/failures omitted")
    accepted = {}
    rejected = {}
    for name, source in sorted(record["cases"].items()):
        if source.get("status") == "rejected":
            if not source.get("diagnostic") or not source.get("sha256"):
                raise RuntimeError(f"lost source diagnostic/provenance: {name}")
            rejected[name] = dict(
                original_git_blob=source["upstream_git_blob"],
                source_sha256=source["sha256"], bytes=source["bytes"],
                failure=source["diagnostic"],
            )
            continue
        if source.get("status") != "accepted":
            raise RuntimeError(f"unclassified source: {name}")
        if not source.get("no_modifications"):
            raise RuntimeError(f"source bytes modified: {name}")
        if not source.get("source_output_sha256") or source["samples_per_mode"] < 4:
            raise RuntimeError(f"missing byte parity evidence: {name}")
        m = source["profile_median"]
        for invocation in ("core", "retry"):
            for field in TIME_FIELDS:
                if not isinstance(m.get(f"pre_{invocation}_{field}_ns"), int):
                    raise RuntimeError(f"missing PRE timing {name}/{invocation}/{field}")
            for field in COUNT_FIELDS:
                if not isinstance(m.get(f"pre_{invocation}_{field}"), int):
                    raise RuntimeError(f"missing PRE count {name}/{invocation}/{field}")
            if m[f"pre_{invocation}_edits"] > m[f"pre_{invocation}_trials"]:
                raise RuntimeError(f"edits exceed trials: {name}")
            if m[f"pre_{invocation}_no_edit_functions"] > m[f"pre_{invocation}_functions"]:
                raise RuntimeError(f"no edits exceed functions: {name}")
        core = {f: m[f"pre_core_{f}"] for f in COUNT_FIELDS}
        phases = {f: m[f"pre_core_{f}_ns"] for f in TIME_FIELDS}
        core_pass = m["core_pre_ns"]
        if core_pass <= 0:
            raise RuntimeError(f"nonpositive PRE timing: {name}")
        expensive = (phases["input_validation"] + phases["final_validation"] +
                     phases["trial_validation"])
        clone = phases["module_clone"] + phases["function_clone"] + phases["trial_clone"]
        accepted[name] = dict(
            original_git_blob=source["upstream_git_blob"],
            source_sha256=source["sha256"], bytes=source["bytes"],
            functions=m["functions"],
            original_object_sha256=source["source_output_sha256"],
            complete_plain_compiler_ms=round(source["compiler_plain_median_ns"]/1e6, 4),
            pre_core_ms=round(core_pass/1e6, 4),
            pre_core_edits=core["edits"],
            pre_core_trials=core["trials"],
            pre_core_rejected_trials=core["rejected_trials"],
            pre_core_examined=core["candidates_examined"],
            pre_core_noedit_functions=core["no_edit_functions"],
            pre_core_verify_ms=round(expensive/1e6, 4),
            pre_core_clone_ms=round(clone/1e6, 4),
            pre_core_analysis_ms=round(phases["analysis"]/1e6, 4),
            pre_core_verifier_share_pct=round(100*expensive/core_pass, 2),
            pre_retry_edits=m["pre_retry_edits"],
        )
        print("CT11_REAL_PRE file=" + name +
              " bytes=" + str(source["bytes"]) +
              " core_edits=" + str(core["edits"]) +
              " trials=" + str(core["trials"]) +
              " noedit_functions=" + str(core["no_edit_functions"]) +
              " verified_ns=" + str(expensive) +
              " clone_ns=" + str(clone) +
              " analysis_ns=" + str(phases["analysis"]) +
              " pre_total_ns=" + str(core_pass), flush=True)
    if len(accepted) != record["accepted"] or len(rejected) != record["rejected"]:
        raise RuntimeError("parsed PRE source results do not match original cohort")
    if not accepted:
        raise RuntimeError("zero actual source successes; cannot profile PRE")
    summary = dict(
        scope="full unmodified upstream musl C source, compiled through Mini-C TypeId",
        upstream_commit=record["upstream_commit"],
        compiler_commit=record["compiler_commit"],
        source_corpus_sha256=record["corpus_sha256"],
        attempted=27, accepted=len(accepted), rejected=len(rejected),
        successes_larger_than_300_bytes=sum(x["bytes"] >= 300 for x in accepted.values()),
        cases_with_pre_edits=sum(x["pre_core_edits"] > 0 for x in accepted.values()),
        total_pre_edits=sum(x["pre_core_edits"] for x in accepted.values()),
        total_pre_trials=sum(x["pre_core_trials"] for x in accepted.values()),
        accepted_cases=accepted, rejected_cases=rejected,
        source_fidelity="all 27 original git-blob-verified, all accepted ELF profile/ordinary byte parity",
        limitation="only accepted source files have PRE timings; counts for rejected sources are unknown, not zero",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    print("CT11_REAL_PRE_SUMMARY attempted=27 accepted="+str(len(accepted))+
          " rejected="+str(len(rejected))+
          " positive_tus="+str(summary["cases_with_pre_edits"])+
          " total_edits="+str(summary["total_pre_edits"])+
          " total_trials="+str(summary["total_pre_trials"])+
          " corpus_sha256="+summary["source_corpus_sha256"], flush=True)
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--census",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    analyze(args.census,args.output)

if __name__=="__main__":
    main()
