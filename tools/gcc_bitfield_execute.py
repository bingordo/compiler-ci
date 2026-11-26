"""Execute pinned GCC bitfield torture tests through the same ABI adapters.

Whole-TU executions are deliberately not mixed-object ABI directions.
The originals remain untouched; unsupported directives/sidecars fail closed.
"""
import re
from pathlib import Path

EXECUTE_DIR = "gcc/testsuite/gcc.c-torture/execute"
CASES = ("bf-layout-1.c", *(f"bitfld-{i}.c" for i in range(1, 11)),
         "bitfld-signed1-1.c")


def discover(root, target):
    """Retain all selected originals, including explicitly unsupported ones."""
    result = []
    for name in CASES:
        source = root / name
        if not source.is_file():
            raise ValueError("missing pinned GCC bitfield source: " + name)
        reason = None
        if source.with_suffix(".x").exists():
            reason = "Tcl sidecar"
        for line in source.read_text().splitlines():
            if not re.search(r"\bdg-[\w-]+", line):
                continue
            if (line.strip() == '/* { dg-require-effective-target int32plus } */'
                    and re.fullmatch(r"x86_64-[A-Za-z0-9_-]*linux[A-Za-z0-9_-]*", target)):
                continue
            reason = "unhandled directive: " + line.strip()
        result.append((source, reason))
    return result


def run_single(source, reason, work, cc, flags, adapters, build_native,
               link_run, observed, command, succeeds):
    """Use identical reference execution and native failure semantics as triads."""
    record = {"case": source.stem, "gcc_flags": flags,
              "origin": "upstream-bitfield-execute", "frontends": {}}
    if reason:
        record.update(status="UNSUPPORTED", reason=reason)
        return record
    work.mkdir(parents=True, exist_ok=True)
    obj = work / "reference.o"
    result = command([cc, *flags, "-I", str(source.parent),
                      "-c", str(source), "-o", str(obj)])
    if not succeeds(result):
        record.update(status="REFERENCE_COMPILE_FAILED", result=result)
        return record
    status, result = link_run(cc, [obj], work / "reference")
    if status != "EXECUTED" or not succeeds(result):
        record.update(status="REFERENCE_" + ("LINK_FAILED" if status != "EXECUTED"
                                             else "RUN_FAILED"), result=result)
        return record
    record.update(status="REFERENCE_PASS", reference=observed(result))
    for name, argv in adapters.items():
        obj = work / (name + ".o")
        status, result = build_native(source, obj, argv, cc, flags, source.parent)
        entry = {"builds": {"whole-tu": {"status": status, "result": result}},
                 "directions": {}}
        record["frontends"][name] = entry
        if status != "PASS":
            entry["directions"]["whole-tu"] = {"status": "NOT_ADMITTED",
                                                "missing_roles": ["whole-tu"]}
            continue
        status, result = link_run(cc, [obj], work / name)
        if status == "EXECUTED":
            status = ("PASS" if observed(result) == record["reference"]
                      else "ABI_MISMATCH")
        entry["directions"]["whole-tu"] = {"status": status, "result": result}
    return record
