#!/usr/bin/env python3
"""Run original GCC compatibility triads against configurable compiler adapters.

This first DejaGNU subset handles plain C compatibility triads. Unsupported
directives and sidecar Tcl scripts are reported, never silently ignored.
Each source role is compiled once per compiler/options combination; all six
mixed assignments are linked and executed against an independent GCC oracle.
"""
import argparse
from itertools import combinations
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import subprocess
import sys

from gcc_compat_abi_pilot import GCC_REVISION, GCC_COMPAT_DIR, command, succeeds
from gcc_compat_inventory import inventory
from gcc_compat_return_probes import generate
from gcc_compat_bitfield_layout import generate_layout, layout_directive
from gcc_bitfield_execute import EXECUTE_DIR, discover, run_single

ROLES = ("main", "x", "y")


def unsupported(case, root, target=None):
    """Reject test semantics that this subset cannot faithfully interpret."""
    if case["missing_roles"]:
        return "missing roles: " + ", ".join(case["missing_roles"])
    if (root / (case["case"] + ".x")).exists():
        return "Tcl sidecar"
    for role in case["roles"].values():
        for line in role["directive_lines"]:
            if case.get("layout_directive_source") and layout_directive(
                    line, Path(case["layout_directive_source"]), target or ""):
                continue
            # Support simple target-only skip selectors. More complex Tcl
            # expressions and option conditions remain explicitly unsupported.
            skip = re.fullmatch(r'\s*/\*\s*\{\s*dg-skip-if\s+"[^"\n]*"\s+\{\s*([A-Za-z0-9_.*?+-]+(?:\s+[A-Za-z0-9_.*?+-]+)*)\s*\}\s*\}\s*\*/\s*', line)
            if skip and target is not None:
                if any(fnmatchcase(target, pattern) for pattern in skip[1].split()):
                    return "target exclusion: " + line
                continue
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
    target = subprocess.check_output([cc, '-dumpmachine'], text=True).strip()
    reason = unsupported(case, root, target)
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


def missing_required(records, requirements, frontends):
    """Check explicit previously verified mixed directions, including absent cases."""
    actual = {}
    for record in records:
        for frontend, entry in record["frontends"].items():
            for option in record["gcc_flags"]:
                for direction, result in entry["directions"].items():
                    if result["status"] == "PASS":
                        actual[(frontend, record["case"], option, direction)] = True
    expected = {
        (group["frontend"], case, option, direction)
        for group in requirements if group["frontend"] in frontends
        for case in group["cases"] for option in group["options"]
        for direction in group["directions"]
    }
    return sorted(expected - actual.keys())


def main():
    """Run an explicitly scoped subset and write a report even on test failures."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("checkout", type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--adapters", required=True, type=Path,
                   help="JSON mapping frontend names to argv arrays containing {source} (preprocessed) or {original}, plus {object}")
    p.add_argument("--prefix", action="append")
    p.add_argument("--cc", default="gcc")
    p.add_argument("--include-return-probes", action="store_true")
    p.add_argument("--include-bitfield-layout", action="store_true",
                   help="include original GCC struct-layout-1 bit-field interoperability tests")
    p.add_argument("--include-bitfield-execute", action="store_true",
                   help="run original GCC standalone bit-field execution tests")
    p.add_argument("--layout-batches", type=int, default=6,
                   help="number of deterministic bit-field batches (0 = all)")
    p.add_argument("--compiler-sha", help="exact tested source revision")
    p.add_argument("--required", type=Path, help="verified mixed-direction regression manifest")
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
    sources = [(root, case, "upstream") for case in cases]
    if args.include_return_probes:
        probe_root = work / "generated"
        generate(probe_root)
        sources.extend((probe_root, case, "generated-return-probe")
                       for case in inventory(probe_root, "probe-return-"))
    layout_inventory = None
    if args.include_bitfield_layout:
        # Original checked-in one-bitfield layout test and full upstream
        # generated batches. Keep their source origin separate from probes.
        fixed = inventory(root, "struct-layout-1")
        for case in fixed:
            case["layout_directive_source"] = str(root)
        sources.extend((root, case, "upstream-bitfield-layout") for case in fixed)
        layout_root, layout_cases, layout_inventory = generate_layout(
            root, work, args.cc, args.layout_batches)
        sources.extend((layout_root, case, "upstream-generated-bitfield-layout")
                       for case in layout_cases)
    singles = []
    if args.include_bitfield_execute:
        execute_root = checkout / EXECUTE_DIR
        if subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain",
                                    "--untracked-files=all", "--", EXECUTE_DIR], text=True):
            p.error("GCC bitfield execute directory is dirty")
        target = subprocess.check_output([args.cc, "-dumpmachine"], text=True).strip()
        singles = discover(execute_root, target)
    records = []
    for opt in ("-O0", "-O2"):
        flags = ["-std=gnu11", opt, "-w", "-fno-pie"]
        for case_root, case, origin in sources:
            case_flags = flags + (["-Wno-abi", "-mno-mmx"]
                                  if case.get("layout_directive_source") else [])
            result = run_case(case, case_root, work / opt[1:] / case["case"],
                              args.cc, case_flags, adapters)
            result["origin"] = origin
            records.append(result)
            print(f"{opt} {case['case']}: {result['status']}", flush=True)
            for name, entry in result["frontends"].items():
                print(name, "builds", {k: b["status"] for k, b in entry["builds"].items()},
                      "directions", {k: d["status"] for k, d in entry["directions"].items()}, flush=True)
            (work / "report.json").write_text(json.dumps({
                "gcc_revision": revision, "records": records,
                "compiler_sha": args.compiler_sha,
                "planned_records": 2 * (len(sources) + len(singles)),
                "bitfield_layout_inventory": layout_inventory,
            }, indent=2) + "\n")
        for source, reason in singles:
            result = run_single(source, reason, work / opt[1:] / source.stem,
                                args.cc, flags, adapters, build_native, link_run,
                                observed, command, succeeds)
            records.append(result)
            print(f"{opt} {source.stem}: {result['status']}", flush=True)
            for name, entry in result["frontends"].items():
                print(name, "builds", {k: b["status"] for k, b in entry["builds"].items()},
                      "directions", {k: d["status"] for k, d in entry["directions"].items()}, flush=True)
            (work / "report.json").write_text(json.dumps({
                "gcc_revision": revision, "records": records,
                "compiler_sha": args.compiler_sha,
                "planned_records": 2 * (len(sources) + len(singles)),
                "bitfield_layout_inventory": layout_inventory,
            }, indent=2) + "\n")
    if args.required:
        required = json.loads(args.required.read_text())
        if required["gcc_revision"] != revision:
            p.error("required coverage manifest targets another GCC revision")
        missing = missing_required(records, required["requirements"], adapters)
        report = json.loads((work / "report.json").read_text())
        report["required_missing"] = missing
        report["required_source_run"] = required["source_run"]
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print("REQUIRED_MISSING", json.dumps(missing), flush=True)
        if missing:
            return 1
    if not any(r["status"] == "REFERENCE_PASS" for r in records):
        return 1
    return int(any(fatal(r) for r in records))


if __name__ == "__main__":
    sys.exit(main())
