#!/usr/bin/env python3
"""Run original GCC compatibility triads against configurable compiler adapters.

This first DejaGNU subset handles plain C compatibility triads. Unsupported
directives and sidecar Tcl scripts are reported, never silently ignored.
Each source role is compiled once per compiler/options combination; all six
mixed assignments are linked and executed against an independent GCC oracle.
"""
import argparse
from itertools import combinations
import json
from pathlib import Path
import re
import subprocess
import sys

from gcc_compat_abi_pilot import GCC_REVISION, GCC_COMPAT_DIR, command, succeeds
from gcc_compat_inventory import inventory

ROLES = ("main", "x", "y")


def unsupported(case, root):
    """Reject test semantics that this subset cannot faithfully interpret."""
    if case["missing_roles"]:
        return "missing roles: " + ", ".join(case["missing_roles"])
    if (root / (case["case"] + ".x")).exists():
        return "Tcl sidecar"
    for role in case["roles"].values():
        for line in role["directive_lines"]:
            # Only unconditional dg-do run is redundant for compatibility.
            if not re.fullmatch(r"\s*/\*\s*\{\s*dg-do\s+run\s*\}\s*\*/\s*", line):
                return "unhandled directive: " + line
    return None


def build_native(source, obj, argv, cc, flags, root):
    """Preprocess without rewriting source and invoke a thin frontend adapter."""
    obj.unlink(missing_ok=True)
    processed = obj.with_suffix(".i.c")
    if any("{source}" in arg for arg in argv):
        pp = command([cc, *flags, "-E", "-P", "-I", str(root),
                      str(source), "-o", str(processed)])
        if not succeeds(pp):
            return "PREPROCESS_FAILED", pp
    result = command([arg.replace("{source}", str(processed))
                      .replace("{original}", str(source))
                      .replace("{object}", str(obj)) for arg in argv], timeout=60)
    if not succeeds(result):
        status = ("COMPILER_CRASH" if result["exit_code"] is None
                  or result["exit_code"] < 0 or result["exit_code"] == 101
                  else "COMPILE_REJECTED")
        return status, result
    if not obj.is_file() or not obj.stat().st_size:
        return "EMPTY_OBJECT", result
    return "PASS", result


def link_run(cc, objects, executable):
    """Link with the host C runtime and execute, retaining both diagnostics."""
    link = command([cc, "-no-pie", *map(str, objects), "-o", str(executable)])
    if not succeeds(link):
        return "LINK_FAILED", link
    return "EXECUTED", command([str(executable)])


def observed(result):
    """Extract only externally observable process results."""
    return {k: result[k] for k in ("exit_code", "stdout", "stderr")}


def run_case(case, root, work, cc, flags, adapters):
    """Compile originals and exercise every nonempty proper role substitution."""
    record = {"case": case["case"], "gcc_flags": flags, "frontends": {}}
    reason = unsupported(case, root)
    if reason:
        record.update(status="UNSUPPORTED", reason=reason)
        return record
    work.mkdir(parents=True, exist_ok=True)
    gcc_objects = {}
    sources = {r: root / case["roles"][r]["file"] for r in ROLES}
    for role, source in sources.items():
        obj = work / f"{role}.gcc.o"
        result = command([cc, *flags, "-I", str(root), "-c", str(source), "-o", str(obj)])
        if not succeeds(result):
            record.update(status="REFERENCE_COMPILE_FAILED", result=result)
            return record
        gcc_objects[role] = obj
    status, result = link_run(cc, gcc_objects.values(), work / "reference")
    if status != "EXECUTED" or not succeeds(result):
        record.update(status="REFERENCE_" + ("LINK_FAILED" if status != "EXECUTED"
                                               else "RUN_FAILED"), result=result)
        return record
    record.update(status="REFERENCE_PASS", reference=observed(result))
    for name, argv in adapters.items():
        native = {}
        entry = {"builds": {}, "directions": {}}
        record["frontends"][name] = entry
        for role, source in sources.items():
            obj = work / f"{role}.{name}.o"
            status, result = build_native(source, obj, argv, cc, flags, root)
            entry["builds"][role] = {"status": status, "result": result}
            if status == "PASS":
                native[role] = obj
        for count in (1, 2):
            for roles in combinations(ROLES, count):
                label = "+".join(roles)
                missing = [r for r in roles if r not in native]
                if missing:
                    entry["directions"][label] = {
                        "status": "NOT_ADMITTED", "missing_roles": missing}
                    continue
                objects = [native[r] if r in roles else gcc_objects[r] for r in ROLES]
                status, result = link_run(cc, objects, work / f"{name}-{label}")
                if status == "EXECUTED":
                    status = ("PASS" if observed(result) == record["reference"]
                              else "ABI_MISMATCH")
                entry["directions"][label] = {"status": status, "result": result}
    return record


def fatal(record):
    """Distinguish missing source admission from failures of admitted tests."""
    if record["status"] not in ("UNSUPPORTED", "REFERENCE_PASS"):
        return True
    for frontend in record["frontends"].values():
        if any(b["status"] not in ("PASS", "COMPILE_REJECTED")
               for b in frontend["builds"].values()):
            return True
        if any(d["status"] not in ("PASS", "NOT_ADMITTED")
               for d in frontend["directions"].values()):
            return True
    return False


def main():
    """Run an explicitly scoped subset and write a report even on test failures."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("checkout", type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--adapters", required=True, type=Path,
                   help="JSON mapping frontend names to argv arrays containing {source} (preprocessed) or {original}, plus {object}")
    p.add_argument("--prefix", action="append")
    p.add_argument("--cc", default="gcc")
    args = p.parse_args()
    checkout = args.checkout.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if revision != GCC_REVISION:
        p.error(f"expected pinned GCC revision {GCC_REVISION}")
    root = checkout / GCC_COMPAT_DIR
    # Refuse modified sources, including untracked files which could affect includes.
    if subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain",
                                "--untracked-files=all", "--", GCC_COMPAT_DIR], text=True):
        p.error("GCC compatibility source directory is dirty")
    adapters = json.loads(args.adapters.read_text())
    if not isinstance(adapters, dict) or not adapters:
        p.error("at least one adapter is required")
    for name, argv in adapters.items():
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name == "gcc"
                or not isinstance(argv, list) or not argv
                or not all(isinstance(arg, str) for arg in argv)
                or not any("{object}" in arg for arg in argv)
                or not any("{source}" in arg or "{original}" in arg for arg in argv)):
            p.error(f"invalid adapter {name}")
    cases = [case for prefix in (args.prefix or ["struct-return-", "union-return-"])
             for case in inventory(root, prefix)]
    if not cases or len({c["case"] for c in cases}) != len(cases):
        p.error("empty or overlapping corpus selection")
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    records = []
    for opt in ("-O0", "-O2"):
        flags = ["-std=gnu11", opt, "-w", "-fno-pie"]
        for case in cases:
            result = run_case(case, root, work / opt[1:] / case["case"],
                              args.cc, flags, adapters)
            records.append(result)
            print(f"{opt} {case['case']}: {result['status']}", flush=True)
            (work / "report.json").write_text(json.dumps({
                "gcc_revision": revision, "records": records,
                "planned_records": 2 * len(cases),
            }, indent=2) + "\n")
    if not any(r["status"] == "REFERENCE_PASS" for r in records):
        return 1
    return int(any(fatal(r) for r in records))


if __name__ == "__main__":
    sys.exit(main())
