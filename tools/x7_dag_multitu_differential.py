#!/usr/bin/env python3
"""Source-pinned TypeId DAG A/B: independently linked multi-TU C execution.

Generate three separate C translation units per seed, including cross-TU
8-argument calls, signed wraparound, nested branches and proven ADD DAG
candidates. Compare GCC -fwrapv against independently compiled default and
opt-in TypeId objects. No LTO, no fabricated IR, and no in-memory stub calls.
"""
import argparse
import hashlib
import json
import random
import subprocess
import tempfile
from pathlib import Path

SEEDS=range(20261120,20261140)
MODES=(("plain",False,False,"obj"),
       ("pic_frame",True,True,"obj"),
       ("gas",False,False,"asm"))
TEST_VALUES=(-2147483648,-2147483647,-1073741824,-65536,-101,-7,-1,0,
             1,2,13,127,65535,1073741824,2147483647)
HARNESS=r"""
#include <stdint.h>
#include <stdio.h>
extern int kernel(int,int);
static int (*volatile call)(int,int)=kernel;
int main(void) {
    static const int v[]={VALUES};
    uint64_t hash=1469598103934665603ULL;
    for(unsigned i=0;i<sizeof(v)/sizeof(v[0]);i++)
      for(unsigned j=0;j<sizeof(v)/sizeof(v[0]);j++) {
        uint32_t z=(uint32_t)call(v[i],v[j]);
        hash=(hash^z)*1099511628211ULL;
      }
    uint32_t seed=0xc1a5f00du;
    for(unsigned i=0;i<1024;i++) {
        seed=1664525u*seed+1013904223u;
        int x=(int32_t)seed;
        seed=1664525u*seed+1013904223u;
        int y=(int32_t)seed;
        hash=(hash^(uint32_t)call(x,y))*1099511628211ULL;
    }
    printf("%llu\n",(unsigned long long)hash);
    return 0;
}
""".replace("VALUES",",".join(str(x) for x in TEST_VALUES))


def generate(seed):
    r=random.Random(seed)
    c=[r.randint(-19,19) for _ in range(8)]
    # Parenthesized signed constants prevent x--1 token ambiguity.
    left="""int left_add(int x,int y){return x-(0-y);}
int add8(int a0,int a1,int a2,int a3,int a4,int a5,int a6,int a7) {
    return ((a0+a1) ^ (a2-a3)) + ((a4+a5) ^ (a6+a7));
}
"""
    mid=f"""extern int left_add(int,int);
extern int add8(int,int,int,int,int,int,int,int);
int right_add(int x,int y) {{ return x - (-1*y); }}
int bridge(int x,int y) {{
    int a=left_add(x,y);
    int b=right_add(y,x);
    int z=add8(x,y,a,b,x+({c[0]}),y-({c[1]}),a^b,b+a);
    if (z>({c[2]})) return z+({c[3]});
    return z-({c[4]});
}}
"""
    top=f"""extern int left_add(int,int);
extern int right_add(int,int);
extern int bridge(int,int);
int kernel(int x,int y) {{
    int a=bridge(x,y);
    int b=right_add(x+({c[5]}),y-({c[6]}));
    if ((a & ({c[7]})) == 0) return (a^b) + left_add(x,y);
    return (a-b) ^ right_add(y,x);
}}
"""
    return {"left":left,"middle":mid,"entry":top}


def run(args, timeout=50):
    proc=subprocess.run([str(x) for x in args],text=True,capture_output=True,
                        timeout=timeout,check=False)
    if proc.returncode:
        raise RuntimeError(f"exit={proc.returncode} command={args!r}\n"
                           f"stdout={proc.stdout[-600:]}\n"
                           f"stderr={proc.stderr[-2400:]}")
    return proc.stdout.strip()


def text_bytes(path):
    output=run(["objdump","-h",path])
    for line in output.splitlines():
        fields=line.split()
        if len(fields)>=3 and fields[1]==".text":
            return int(fields[2],16)
    raise RuntimeError(f"{path}: no ELF .text")


def gcc_syntax_check():
    for seed in SEEDS:
        files=generate(seed)
        for name,source in files.items():
            proc=subprocess.run(["gcc","-fsyntax-only","-std=c11",
                                 "-fwrapv","-Wall","-Wextra","-Werror",
                                 "-x","c","-"],input=source,text=True,
                                 capture_output=True,timeout=10,check=False)
            if proc.returncode:
                raise RuntimeError(f"{seed}/{name}: {proc.stderr[:1400]}")
    return True


def execute(default,optin,out):
    default,optin=Path(default).resolve(),Path(optin).resolve()
    out=Path(out).resolve()
    out.parent.mkdir(parents=True,exist_ok=True)
    assert default.is_file() and optin.is_file()
    gcc_syntax_check()
    rows=[]
    with tempfile.TemporaryDirectory() as directory:
        tmp=Path(directory)
        host=tmp/"host.c"
        host.write_text(HARNESS)
        for seed in SEEDS:
            source=generate(seed)
            files={name:tmp/f"{seed}-{name}.c" for name in source}
            for name,path in files.items():
                path.write_text(source[name])
            ref_objs=[]
            for name,src in files.items():
                obj=tmp/f"{seed}-{name}-gcc.o"
                run(["gcc","-O2","-fwrapv","-fno-lto","-fno-builtin",
                     "-std=c11","-c",src,"-o",obj])
                ref_objs.append(obj)
            expected_exe=tmp/f"{seed}-gcc.exe"
            run(["gcc","-O2","-no-pie",host,*ref_objs,"-o",expected_exe])
            expected=run([expected_exe],timeout=20)
            for label,pic,frame,sink in MODES:
                # GAS the first and last seed, enough to exercise the same
                # multi-TU linking ABI without tripling entire corpus work.
                if sink=="asm" and seed not in (SEEDS.start, SEEDS.stop-1):
                    continue
                mode={}
                for variant,driver in (("default",default),("optin",optin)):
                    objs=[]
                    for name,src in files.items():
                        ext="s" if sink=="asm" else "o"
                        generated=tmp/f"{seed}-{label}-{variant}-{name}.{ext}"
                        cmd=[driver,src,"--language","mini-c","--ir","typeid",
                             "--emit",sink,"--target","x86_64","-o",generated]
                        if pic:cmd.append("--pic")
                        if frame:cmd.append("--frame-pointer")
                        run(cmd)
                        obj=generated
                        if sink=="asm":
                            obj=tmp/f"{seed}-{label}-{variant}-{name}.o"
                            run(["gcc","-c",generated,"-o",obj])
                        objs.append(obj)
                    exe=tmp/f"{seed}-{label}-{variant}.exe"
                    run(["gcc","-O2","-no-pie",host,*objs,"-o",exe])
                    actual=run([exe],timeout=20)
                    if actual!=expected:
                        raise RuntimeError(f"{seed}/{label}/{variant}: "
                                           f"native mismatch: {actual} != {expected}")
                    mode[variant]={
                        "text_bytes":sum(text_bytes(obj) for obj in objs),
                        "object_sha256":[hashlib.sha256(obj.read_bytes()).hexdigest() for obj in objs],
                    }
                saved=mode["default"]["text_bytes"]-mode["optin"]["text_bytes"]
                rows.append({"seed":seed,"mode":label,"sink":sink,
                             "pic":pic,"frame_pointer":frame,
                             "source_sha256":{k:hashlib.sha256(v.encode()).hexdigest()
                                              for k,v in source.items()},
                             "default":mode["default"],"optin":mode["optin"],
                             "saved_bytes":saved,"gcc_oracle_equal":True})
                print("X7_MTU_CASE",seed,label,"before",
                      mode["default"]["text_bytes"],"after",
                      mode["optin"]["text_bytes"],"saved",saved,flush=True)
    reductions=sum(r["saved_bytes"]>0 for r in rows)
    increases=sum(r["saved_bytes"]<0 for r in rows)
    report={
        "schema_version":1,
        "kind":"x7_production_typeid_generated_dag_three_tu_native_gcc_differential",
        "seed_start":SEEDS.start,"seed_stop_exclusive":SEEDS.stop,
        "separate_translation_units":3,
        "cases":len(rows),"unique_seeds":len(SEEDS),
        "selected_modes":[list(x) for x in MODES],
        "cross_tu_calls":True,"sysv_8_arg_call":True,"no_lto":True,
        "source_validation":"gcc -fsyntax-only -std=c11 -fwrapv -Wall -Wextra -Werror",
        "native_oracle":"gcc -O2 -fwrapv (separately compiled and linked)",
        "rows":rows,"native_reference_agreement":True,
        "text_reductions":reductions,"text_increases":increases,
        "net_saved_text_bytes":sum(r["saved_bytes"] for r in rows),
        "compiler_default_changed":False,
        "new_source_proof_or_rule":False,
    }
    out.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print("X7_MTU_SUMMARY",json.dumps({
        "cases":len(rows),"seeds":len(SEEDS),"saved_bytes":report["net_saved_text_bytes"],
        "reductions":reductions,"increases":increases,
    },sort_keys=True),flush=True)
    if len(rows)!=42: raise RuntimeError("incomplete matrix: expected 40 object + 2 GAS cases")
    if increases:raise RuntimeError("code size regression on some multi-TU workload")
    if reductions==0:raise RuntimeError("no generated source DAG change reached real multi-TU")
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--default")
    p.add_argument("--optin")
    p.add_argument("--out")
    p.add_argument("--syntax-only",action="store_true")
    args=p.parse_args()
    if args.syntax_only:
        gcc_syntax_check()
        print("X7_MTU_SOURCE_SYNTAX_OK",len(SEEDS),3)
    else:
        if not all((args.default,args.optin,args.out)):
            p.error("--default, --optin, and --out are required for native execution")
        execute(args.default,args.optin,args.out)
