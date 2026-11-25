#!/usr/bin/env python3
"""Classify pinned upstream GCC cross-compiler ABI tests against Mini-C.

Downloads original GPL-licensed GCC test files only into a temporary CI work
directory. No GPL sources are copied into this repository. Every byte is
verified against the immutable Git blob IDs recorded below. A Mini-C compile
rejection is COMPILE_REJECTED, never an ABI pass; accepted objects must link
and execute identically to an independent GCC/GCC reference.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

GCC_REVISION = "1330eed38849ad186518f609c52bd5651c2bfdc1"
GCC_COMPAT_DIR = "gcc/testsuite/gcc.dg/compat"
# A small initial selection of unmodified upstream ABI triads, not a claim
# that Mini-C has admitted these source-language features.
PILOTS = {
    "scalar-by-value-2": {
        "scalar-by-value-2_main.c": "8426b789434ecde3bb921f4c02a90ebff7d2064f",
        "scalar-by-value-2_x.c": "48ea1d9975ffc4ff0536f42da0eca95276322ff7",
        "scalar-by-value-2_y.c": "2eea6135d74c293b4848bce530c7935891918107",
    },
    "scalar-return-1": {
        "scalar-return-1_main.c": "0a27f5416b1d84157e7077fc83374bd49eba28dd",
        "scalar-return-1_x.c": "5556f9510a3efe9b6eeb908ec6ad3c085a9d13cd",
        "scalar-return-1_y.c": "f913dbbb8917a05f6f155cc743669615e3e45ba7",
    },
    # Validate original GCC aggregate/global layout and pointer address use.
    "sdata-1": {
        "sdata-1_main.c": "68e9f8f09ad3a9c82d0d0fae86f574b516a9d2cf",
        "sdata-1_x.c": "fc7b8cef333b50df859dc40109e6e1a01a77cca9",
        "sdata-1_y.c": "8bc878a85d264b98e6e455d6cd47de9a6e1a23b2",
    },
    # GCC PR83487: empty/overaligned structs interleaved with GP stack args.
    "pr83487-1": {
        "pr83487-1_main.c": "a99519f843346b42bf63cb28ca27c3583a776f48",
        "pr83487-1_x.c": "22b71cf185629b823cb48c746ba67a2e1d2130bd",
        "pr83487-1_y.c": "cf275d8f33cf7dc5505d66e2a8c6d2fbe1826efc",
    },
}
COMMON_FILES = {"compat-common.h": "4b3fb9c14cab4ad96555d85ecdbae68da4a67735"}
ADDITIONAL_HEADERS = {
    "sdata-1": {
        "sdata-section.h": "15be8438662afd1738a0a97a472710291f690167",
    },
    "pr83487-1": {
        "pr83487-1.h": "f39d5fd89dd3eb97cb052ed122ad17b6fd1b4998",
    },
}
# These unmodified original GCC sources passed as mixed GCC/Mini-C objects
# on compiler/main in compiler-ci run #45547473336. Enforce that coverage.
REQUIRED_ADMISSIONS = {
    ("scalar-by-value-2", "main"),
    ("scalar-return-1", "main"),
    ("sdata-1", "x"),
    ("sdata-1", "y"),
}


def git_blob_id(data: bytes) -> str:
    """Compute the Git object ID for the exact downloaded file bytes."""
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def download_verified(name: str, expected: str, root: Path, offline: Path | None) -> Path:
    """Fetch one immutable GCC source and fail closed on content mismatches."""
    if offline:
        data = (offline / name).read_bytes()
    else:
        url = (f"https://raw.githubusercontent.com/gcc-mirror/gcc/"
               f"{GCC_REVISION}/{GCC_COMPAT_DIR}/{name}")
        with urlopen(url, timeout=25) as response:
            data = response.read(512 * 1024 + 1)
    if len(data) > 512 * 1024 or git_blob_id(data) != expected:
        raise ValueError(f"upstream GCC file hash mismatch: {name}")
    path = root / name
    path.write_bytes(data)
    return path


def command(argv: list[str], *, timeout: int = 45) -> dict:
    """Execute a bounded build or test command, retaining reproducible logs."""
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout,
                              check=False)
        return {
            "argv": argv,
            "exit_code": done.returncode,
            "stdout": done.stdout.decode("utf-8", "replace")[:16000],
            "stderr": done.stderr.decode("utf-8", "replace")[:16000],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "argv": argv, "exit_code": None,
            "stdout": "", "stderr": f"timed out after {error.timeout}s",
        }


def succeeds(result: dict) -> bool:
    """Treat negative exit status and timeout as failures, never as passes."""
    return result["exit_code"] == 0


def run_case(name: str, compiler: Path, cc: str, root: Path,
             offline: Path | None) -> dict:
    """Run GCC/GCC, then replace each source role with one Mini-C object."""
    base = name + "_"
    home = root / name
    home.mkdir(parents=True, exist_ok=True)
    for file, blob in {**COMMON_FILES, **ADDITIONAL_HEADERS.get(name, {}),
                       **PILOTS[name]}.items():
        download_verified(file, blob, home, offline)
    record = {"case": name, "gcc_revision": GCC_REVISION,
              "directions": {}, "gcc_reference": "NOT_RUN"}
    gcc_objects = {}
    for role in ("main", "x", "y"):
        source = home / f"{base}{role}.c"
        obj = home / f"{role}.gcc.o"
        result = command([cc, "-std=gnu11", "-O0", "-w", "-fno-pie",
                          "-c", str(source), "-o", str(obj)])
        if not succeeds(result):
            record["gcc_reference"] = "REFERENCE_COMPILE_FAILED"
            record["reference_failure"] = result
            return record
        gcc_objects[role] = obj
    reference = home / "reference-gcc"
    link = command([cc, "-no-pie", *map(str, gcc_objects.values()),
                    "-o", str(reference)])
    if not succeeds(link):
        record["gcc_reference"] = "REFERENCE_LINK_FAILED"
        record["reference_failure"] = link
        return record
    reference_run = command([str(reference)])
    if not succeeds(reference_run):
        record["gcc_reference"] = "REFERENCE_RUN_FAILED"
        record["reference_failure"] = reference_run
        return record
    record["gcc_reference"] = "PASS"
    record["reference_output"] = {
        "exit_code": reference_run["exit_code"],
        "stdout": reference_run["stdout"],
        "stderr": reference_run["stderr"],
    }
    for replacement in ("main", "x", "y"):
        entry = {"status": "NOT_RUN"}
        record["directions"][replacement] = entry
        source = home / f"{base}{replacement}.c"
        processed = home / f"{replacement}.preprocessed.c"
        # Preprocessed C can exceed ordinary diagnostic log limits, notably
        # when GCC expands <stdarg.h>; stream the full source into a file.
        preproc_args = [cc, "-std=gnu11", "-E", "-P", "-w",
                        "-I", str(home), str(source)]
        try:
            with processed.open("wb") as output:
                preproc_run = subprocess.run(
                    preproc_args, stdout=output, stderr=subprocess.PIPE,
                    timeout=45, check=False)
            preproc = {
                "argv": preproc_args, "exit_code": preproc_run.returncode,
                "stderr": preproc_run.stderr.decode("utf-8", "replace")[:16000],
            }
        except subprocess.TimeoutExpired:
            preproc = {"argv": preproc_args, "exit_code": None,
                       "stderr": "preprocessor timed out"}
        if not succeeds(preproc):
            entry.update(status="PREPROCESS_FAILED", result=preproc)
            continue
        obj = home / f"{replacement}.mini.o"
        build = command([str(compiler), str(processed), "--emit", "obj",
                         "-o", str(obj)], timeout=60)
        if not succeeds(build):
            entry.update(status=("COMPILER_CRASH" if build["exit_code"] is None
                                 or build["exit_code"] < 0 else "COMPILE_REJECTED"),
                         result=build)
            continue
        if not obj.exists() or obj.stat().st_size == 0:
            entry.update(status="EMPTY_OBJECT", result=build)
            continue
        objects = [obj if role == replacement else gcc_objects[role]
                   for role in ("main", "x", "y")]
        executable = home / f"mixed-{replacement}"
        mixed_link = command([cc, "-no-pie", *map(str, objects),
                              "-o", str(executable)])
        if not succeeds(mixed_link):
            entry.update(status="MIXED_LINK_FAILED", result=mixed_link)
            continue
        mixed = command([str(executable)])
        observed = {key: mixed[key] for key in ("exit_code", "stdout", "stderr")}
        entry["observed"] = observed
        if observed == record["reference_output"]:
            entry["status"] = "PASS"
        else:
            entry["status"] = "ABI_MISMATCH"
    return record


def main() -> int:
    """Report admitted coverage honestly and fail any demonstrated mismatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--offline-source-dir", type=Path)
    parser.add_argument("--require-admitted", action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    records = []
    fatal = False
    admitted = 0
    try:
        for name in PILOTS:
            result = run_case(name, args.compiler.resolve(), args.cc,
                              args.work_dir, args.offline_source_dir)
            records.append(result)
            if result["gcc_reference"] != "PASS":
                fatal = True
            for direction, value in result["directions"].items():
                outcome = value["status"]
                print(f"GCC_COMPAT {name} {direction}=Mini-C: {outcome}", flush=True)
                if outcome == "COMPILE_REJECTED":
                    diagnostic = value.get("result", {}).get("stderr", "")
                    first = diagnostic.strip().splitlines()[:3]
                    print(f"GCC_COMPAT_DIAGNOSTIC {name} {direction}=Mini-C: "
                          + " | ".join(first)[:600], flush=True)
                admitted += int(outcome == "PASS")
                if outcome in ("ABI_MISMATCH", "MIXED_LINK_FAILED",
                               "COMPILER_CRASH", "EMPTY_OBJECT",
                               "PREPROCESS_FAILED"):
                    fatal = True
    except Exception as error:
        fatal = True
        records.append({"status": "PILOT_SETUP_FAILED", "error": str(error)})
        print(f"GCC_COMPAT setup failure: {error}", file=sys.stderr)
    observed = {
        (record["case"], direction): result["status"]
        for record in records if "case" in record
        for direction, result in record["directions"].items()
    }
    required_missing = sorted(
        f"{case}:{direction}"
        for case, direction in REQUIRED_ADMISSIONS
        if observed.get((case, direction)) != "PASS"
    )
    if required_missing:
        fatal = True
        print("GCC_COMPAT_REQUIRED_MISSING " + ", ".join(required_missing),
              file=sys.stderr)
    summary = {"upstream": f"gcc-mirror/gcc@{GCC_REVISION}",
               "required_admissions": sorted(f"{c}:{r}" for c, r in REQUIRED_ADMISSIONS),
               "required_missing": required_missing,
               "admitted_mixed_directions": admitted,
               "attempted_mixed_directions": 3 * len(PILOTS),
               "cases": records}
    (args.work_dir / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("GCC_COMPAT_SUMMARY " + json.dumps({
        "admitted_mixed_directions": admitted,
        "attempted_mixed_directions": 3 * len(PILOTS),
        "reference_cases_passed": sum(
            r.get("gcc_reference") == "PASS" for r in records),
        "required_missing": required_missing,
        "fatal": fatal,
    }), flush=True)
    if args.require_admitted and not admitted:
        fatal = True
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
