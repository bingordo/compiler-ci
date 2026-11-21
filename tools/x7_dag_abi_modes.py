#!/usr/bin/env python3
"""Differential real TypeId ELF/GAS codegen under PIC and frame-pointer modes.

Both drivers are compiled from exactly the same pinned compiler source, with
the X7 source-DAG Cargo feature off/on. GCC is an independent -fwrapv oracle.
This complements (not replaces) FP, atomic, variadic, GOT/CFI and dlopen tests.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import tempfile

SOURCE = "int kernel(int x,int y){return x-(0-y);}\n"
HOST = r"""
#include <stdint.h>
#include <stdio.h>
extern int kernel(int,int);
static int (*volatile call)(int,int) = kernel;
int main(void) {
  static const int v[] = {-2147483647-1,-2147483647,-65536,-12,-1,0,1,8,17,65535,2147483647};
  uint64_t s=1469598103934665603ULL;
  for(unsigned i=0;i<sizeof(v)/sizeof(v[0]);++i)
    for(unsigned j=0;j<sizeof(v)/sizeof(v[0]);++j)
       s = (s ^ (uint32_t)call(v[i],v[j])) * 1099511628211ULL;
  printf("%llu\n",(unsigned long long)s);
  return 0;
}
"""


def run(argv):
    p = subprocess.run([str(a) for a in argv], text=True, capture_output=True,
                       timeout=60, check=False)
    if p.returncode:
        raise RuntimeError(f"exit={p.returncode} cmd={argv!r} "
                           f"stderr={p.stderr[-2400:]} stdout={p.stdout[-500:]}")
    return p.stdout.strip()


def text_size(path):
    listing = run(["objdump", "-h", path])
    for line in listing.splitlines():
        columns=line.split()
        if len(columns)>=3 and columns[1]==".text":
            return int(columns[2],16)
    raise RuntimeError("no .text in ELF: "+str(path))


def measure(default, optin, out):
    default, optin = Path(default).resolve(), Path(optin).resolve()
    out = Path(out).resolve()
    assert default.is_file() and optin.is_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        temp=Path(directory)
        source=temp/"source.c"; source.write_text(SOURCE)
        host=temp/"harness.c"; host.write_text(HOST)
        oracle=temp/"oracle.o"; run(["gcc","-O2","-fwrapv","-c",source,"-o",oracle])
        reference=temp/"reference"
        run(["gcc","-O2","-no-pie",host,oracle,"-o",reference])
        expected=run([reference])
        for pic,frame,sink in itertools.product((False,True),(False,True),("obj","asm")):
            mode=f"pic{int(pic)}-frame{int(frame)}-{sink}"
            variants={}
            for name,binary in (("baseline",default),("optin",optin)):
                generated=temp/(mode+"-"+name+("."+"o" if sink=="obj" else ".s"))
                argv=[binary,source,"--language","mini-c","--ir","typeid",
                      "--target","x86_64","--emit",sink,"-o",generated]
                if pic:argv.append("--pic")
                if frame:argv.append("--frame-pointer")
                run(argv)
                obj=generated
                if sink=="asm":
                    obj=temp/(mode+"-"+name+".o")
                    run(["gcc","-c",generated,"-o",obj])
                output=temp/(mode+"-"+name)
                run(["gcc","-O2","-no-pie",host,obj,"-o",output])
                observed=run([output])
                if observed!=expected:
                    raise RuntimeError(f"{mode}/{name}: disagrees with GCC ({observed} != {expected})")
                variants[name]={
                    "object_sha256":hashlib.sha256(obj.read_bytes()).hexdigest(),
                    "text_bytes":text_size(obj),
                }
            saved=variants["baseline"]["text_bytes"]-variants["optin"]["text_bytes"]
            rows.append({"mode":mode,"pic":pic,"frame_pointer":frame,
                         "sink":sink,"variants":variants,"saved_text_bytes":saved})
            print("X7_ABI_DAG_MODE",mode,"before",variants["baseline"]["text_bytes"],
                  "after",variants["optin"]["text_bytes"],"saved",saved,flush=True)
    report={"schema_version":1,"kind":"x7_source_dag_abi_mode_native_differential",
            "source_sha256":hashlib.sha256(SOURCE.encode()).hexdigest(),
            "reference":"gcc -O2 -fwrapv independent native oracle",
            "cases":len(rows),"rows":rows,
            "native_reference_parity":True,
            "new_isa_rules_admitted":0,
            "feature_default_changed":False,
            "total_saved_text_bytes":sum(r["saved_text_bytes"] for r in rows),
            "regressions":sum(r["saved_text_bytes"]<0 for r in rows)}
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    if len(rows)!=8 or report["regressions"]:
        raise RuntimeError("PIC/frame/ELF/GAS regression or incomplete matrix")
    if not any(r["pic"] and r["frame_pointer"] and r["saved_text_bytes"]>0 for r in rows):
        raise RuntimeError("feature did not reduce code size in PIC+RBP mode")
    print("X7_ABI_DAG_PASS",len(rows),"total_saved",report["total_saved_text_bytes"])
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--default",required=True)
    p.add_argument("--optin",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    measure(a.default,a.optin,a.out)
