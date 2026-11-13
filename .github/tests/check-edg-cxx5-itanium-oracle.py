#!/usr/bin/env python3
"""Independent G++ Itanium x86-64 virtual table/address-point oracle.

This verifies *GCC's* physical vtable layout; it does not grant EDG's
logical virtual-number records any executable ABI interpretation.
The fixture has one primary virtual base-subobject and two secondary
polymorphic base-subobjects requiring genuine non-virtual thunks.
"""
import pathlib
import re
import subprocess
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("Itanium virtual ABI oracle: " + message)


def extract_table(dump: str, cls: str):
    m = re.search(r"^Vtable for " + re.escape(cls) +
                  r"\n.+?: (\d+) entries\n((?:\d+\s+.*\n)+)",
                  dump, re.MULTILINE)
    require(m is not None, f"missing emitted GCC vtable for {cls}")
    table = {}
    for line in m.group(2).splitlines():
        match = re.match(r"^\s*(\d+)\s+\(int \(\*\)\(\.\.\.\)\)(.*)$", line)
        require(match is not None, f"unsupported GCC vtable line: {line}")
        offset = int(match.group(1))
        require(offset not in table and offset % 8 == 0,
                f"invalid/duplicate byte offset {offset} in {cls}")
        table[offset] = match.group(2).strip()
    require(len(table) == int(m.group(1)),
            f"{cls}: truncated or unexpected GCC vtable entries")
    return table


def require_entry(table, offset, value):
    actual = table.get(offset, "<missing>")
    require(value in actual,
            f"vtable offset {offset}: expected {value!r}, saw {actual!r}")


def check(dump_path: pathlib.Path, object_path: pathlib.Path) -> None:
    dump = dump_path.read_text()
    # G++ places its address point at the function slot, not the start
    # of the table: the two preceding 8-byte entries are top-offset/RTTI.
    base = extract_table(dump, "VRoot")
    require_entry(base, 0, "0")
    require_entry(base, 8, "_ZTI5VRoot")
    require_entry(base, 16, "VRoot::root")

    derived = extract_table(dump, "VDerived")
    require(set(derived) == set(range(0, 14 * 8, 8)),
            "primary/secondary virtual table shape changed")
    for offset, value in (
        (0, "0"), (8, "_ZTI8VDerived"),
        (16, "VDerived::root"), (24, "VDerived::~VDerived"),
        (32, "VDerived::~VDerived"),
        (40, "VDerived::left"), (48, "VDerived::right"),
        (56, "VDerived::extra"),
        (64, "-16"), (72, "_ZTI8VDerived"),
        (80, "_ZThn16_N8VDerived4leftEv"),
        (88, "-32"), (96, "_ZTI8VDerived"),
        (104, "_ZThn32_N8VDerived5rightEv"),
    ):
        require_entry(derived, offset, value)

    c = re.search(r"^Class VDerived\n(.*?)(?=^Class |\Z)",
                  dump, re.MULTILINE | re.DOTALL)
    require(c is not None, "GCC VDerived class layout missing")
    cls = c.group(1)
    require(re.search(r"^\s+size=48 align=8\s*$", cls, re.MULTILINE),
            "GCC 64-bit object layout changed")
    for obj, delta, vptr in (
        ("VDerived", 0, 16),
        ("VRoot", 0, None),
        ("VLeft", 16, 80),
        ("VRight", 32, 104),
    ):
        line = re.search(r"^" + obj + r" \([^\n]+\) " +
                         str(delta) + r"[ \t]*$", cls, re.MULTILINE)
        require(line is not None, f"GCC {obj} base offset must be {delta}")
        if vptr is not None:
            tail = cls[line.end():]
            first = tail.splitlines()[1] if len(tail.splitlines()) > 1 else ""
            require(f"_ZTV8VDerived) + {vptr})" in first,
                    f"{obj}: incorrect physical address point, {first!r}")

    nm = subprocess.check_output(["nm", "-C", str(object_path)],
                                 text=True)
    require("non-virtual thunk to VDerived::left()" in nm,
            "missing -16 this-adjustment thunk")
    require("non-virtual thunk to VDerived::right()" in nm,
            "missing -32 this-adjustment thunk")
    print("GCC_ITANIUM_ORACLE primary_vptr=16 left_base=16 left_vptr=80 "
          "left_thunk=-16 right_base=32 right_vptr=104 right_thunk=-32 "
          "pointer_bytes=8 reference_only=true "
          "edg_machine_abi_proven=false")


if __name__ == "__main__":
    require(len(sys.argv) == 3, "usage: check-gcc-itanium.py dump.class obj.o")
    check(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
