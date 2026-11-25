#!/usr/bin/env python3
"""Investigate why production certified X7 source DAGs yield no native A/B delta.

Read actual post-frontend TypeId snapshots, not synthetic matcher fixtures.
This is a one-shot pinned external CI diagnostic; it does not enable any
feature by default or change the compiled x86 backend.
"""
import argparse
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile

def command(argv):
    result=subprocess.run(argv,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(f"command {argv!r} failed: {result.stderr[-4500:]}")
    return result.stdout

def text_preview(path):
    data=path.read_text(errors="replace")
    matching=[line for line in data.splitlines() if
              re.search(r'(?i)\b(and|xor|or|sub)\b|intbin|bitwise|and32a|xor64|or64',line)]
    print(f"X7_SHAPE_FILE {path.name} bytes={len(data)} matching_lines={len(matching)}")
    for line in matching[:45]:
        print("  "+line[:220])
    if not matching:
        print("  PREVIEW "+data[:1200].replace("\n"," | "))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--compiler",type=Path,required=True)
    options=parser.parse_args()
    compiler=options.compiler.resolve(strict=True)
    spec=importlib.util.spec_from_file_location("x7_ab",Path("../scripts/x7_logic_physical_ab.py").resolve())
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="x7-source-shapes-") as name:
        root=Path(name)
        source=root/"fixture.c"
        source.write_text(module.fixture())
        for opt in ("-O0","-O2"):
            ir=root/(opt[1:]+"-ir")
            ir.mkdir()
            obj=root/(opt[1:]+".o")
            command([str(compiler),str(source),opt,"--language","mini-c",
                     "--target","x86_64","--emit","obj","-o",str(obj),
                     "--dump-ir",str(ir)])
            files=sorted(path for path in ir.rglob("*") if path.is_file())
            print(f"X7_SHAPE_PHASE opt={opt} ir_files={len(files)} "
                  f"names={[p.relative_to(ir).as_posix() for p in files][:24]}")
            for path in (files[:2]+files[-2:] if len(files)>2 else files):
                text_preview(path)
            dis=command(["objdump","-d","--no-show-raw-insn","-M","intel",str(obj)])
            for fun in ("and32a","xor32","and64a","xor64"):
                pattern=r"<"+fun+r">:\n(.*?)(?=\n[0-9a-f]+ <|\Z)"
                match=re.search(pattern,dis,re.S)
                if match:
                    print(f"X7_NATIVE_FUNCTION opt={opt} name={fun}\n"
                          +match.group(1)[:2500])
                else:
                    print(f"X7_NATIVE_FUNCTION_MISSING opt={opt} name={fun}")

if __name__=="__main__":
    main()
