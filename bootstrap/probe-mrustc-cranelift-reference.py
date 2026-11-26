#!/usr/bin/env python3
"""Compare reference code generators on private, unchanged mrustc C units.
Only fixed diagnostic categories and object validation status leave the runner."""
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

mode, driver, root, *extra = sys.argv[1:]
assert mode in ("edg", "minic")
assert Path(driver).is_file()
units = sorted((p for p in Path(root).resolve().rglob("*.c")
                if p.is_file() and p.stat().st_size),
               key=lambda p: (p.stat().st_size, str(p)))
assert units
selected = list(dict.fromkeys([units[0], units[-1]]))
categories = {"preprocessor": r"preprocess", "atomic": r"atomic",
              "syntax": r"syntax|parse|unexpected token",
              "type": r"type|semantic", "unsupported": r"unsupported|not implemented",
              "abi": r"abi|calling convention", "lowering": r"IL lowering failed",
              "panic": r"panicked|segmentation fault"}

def attempt(cmd, log, seconds):
    start = time.monotonic()
    with log.open("wb") as stream:
        try:
            rc = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=seconds).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    return rc, round(time.monotonic()-start, 2)

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    failures = 0
    for i, src in enumerate(selected):
        out = tmp / f"reference{i}.o"
        log = tmp / "reference.log"
        gcc, _ = attempt(["gcc", "-std=gnu11", "-fsyntax-only", str(src)], tmp/"gcc.log", 180)
        cmd = ([driver, "--cpp=cc", "--emit-obj", str(out), str(src)] if mode == "minic"
               else [driver, str(out), *extra, str(src)])
        rc, elapsed = attempt(cmd, log, 300)
        elf = False
        if out.is_file():
            with out.open("rb") as obj:
                elf = obj.read(4) == bytes([127,69,76,70])
        msg = log.read_text(errors="replace")
        codes = sorted(set(re.findall(r"error\[([PLS][0-9]{4})\]", msg)))
        print(f"CLIF_MODE={mode} SAMPLE={i} BYTES={src.stat().st_size} GCC_EXIT={gcc} CLIF_EXIT={rc} ELF={int(elf)} SECONDS={elapsed}", flush=True)
        print("CLIF_DIAG_IDS="+",".join(codes), flush=True)
        print("CLIF_DIAG_CATEGORIES="+",".join(k for k,v in categories.items() if re.search(v,msg,re.I)), flush=True)
        failures += int(gcc != 0 or rc != 0 or not elf)
    raise SystemExit(int(failures != 0))
