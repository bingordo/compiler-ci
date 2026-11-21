#!/usr/bin/env python3
"""Real Mini-C/GCC source differential for TypeId DAG, aliasing and CFG.

Three bounded families: pointer-to-local alias/call barriers, mutable indexed
arrays, and loop-carried diamonds. Every seed is a separately parsed C source.
GCC -O2 -fwrapv is the independent native execution oracle; both real compiler
executables are built from the identical pinned source with DAG feature OFF/ON.
No synthetic IR or proof/admission bypass. Runtime and memory correctness, not
performance, is checked here.
"""
import argparse
import hashlib
import json
import random
import subprocess
import tempfile
from pathlib import Path

SEED=20261120
FAMILIES=("pointer_alias_call","array_overlap","diamond_loop")
CASES_PER_FAMILY=16
VALUES=(-2147483648,-2147483647,-1073741824,-65536,-31,-1,0,1,5,
        17,65535,1073741824,2147483647)
HOST=r"""
#include <stdint.h>
#include <stdio.h>
extern int kernel(int,int);
static int (*volatile call)(int,int)=kernel;
int main(void) {
    static const int v[]={EDGE_VALUES};
    uint64_t hash=1469598103934665603ULL;
    for(unsigned i=0;i<sizeof(v)/sizeof(v[0]);i++)
      for(unsigned j=0;j<sizeof(v)/sizeof(v[0]);j++) {
        uint32_t z=(uint32_t)call(v[i],v[j]);
        hash=(hash^z)*1099511628211ULL;
      }
    uint32_t seed=0x19d10a5bu;
    for(unsigned i=0;i<512;i++) {
        seed=seed*1664525u+1013904223u;
        int x=(int32_t)seed;
        seed=seed*1664525u+1013904223u;
        int y=(int32_t)seed;
        hash=(hash^(uint32_t)call(x,y))*1099511628211ULL;
    }
    printf("%llu\n",(unsigned long long)hash);
    return 0;
}
""".replace("EDGE_VALUES",",".join(str(v) for v in VALUES))


def source_for(family,seed):
    if family not in FAMILIES:
        raise ValueError(family)
    rng=random.Random(SEED+seed+100003*FAMILIES.index(family))
    c=[rng.randint(-16,16) for _ in range(9)]
    k=4+rng.randrange(5)
    if family=="pointer_alias_call":
        return f"""int mutate(int *p,int *q,int v) {{
  int prior=*p;
  *p=prior+v;
  *q=(*q ^ prior)+v;
  return *p-*q;
}}
int kernel(int x,int y) {{
  int a=x,b=y,total=0,i=0;
  int *p=&a;
  int *q=&b;
  if ((x ^ y) & 1) q=p;
  else if (x>y) p=q;
  while(i<{k}) {{
    int before=*p;
    int v=mutate(p,q,y+i+({c[0]}));
    if ((v & 1)!=0) {{
      *q=*q+(x-(0-before));
    }} else {{
      *p=*p^(y+({c[1]}));
    }}
    total=total+(*p ^ *q)+({c[2]});
    i=i+1;
  }}
  return (total+a)-b;
}}
"""
    if family=="array_overlap":
        return f"""int kernel(int x,int y) {{
  int a[8];
  int i=0,total=0;
  while(i<8) {{
    a[i]=x+i*y+({c[0]});
    i=i+1;
  }}
  i=0;
  while(i<8) {{
    int j=i;
    if (i>=4) j=i-4;
    if (((a[i] ^ y) & 1)!=0) {{
      a[j]=a[i]-(0-a[j]);
    }} else {{
      a[i]=a[j]^(x+({c[1]}));
    }}
    total=total+a[i]+a[j]+({c[2]});
    i=i+1;
  }}
  return total+a[0]-a[7];
}}
"""
    return f"""int kernel(int x,int y) {{
  int a=x,b=y,acc=0,i=0,t=0;
  while(i<{k}) {{
    if(a>b) {{
      t=a-(0-b);
      a=t^(i+({c[0]}));
    }} else {{
      t=b-(-1*a);
      b=t-(i+({c[1]}));
    }}
    if ((t & 2)!=0) {{
      acc=acc+t+({c[2]});
      b=b+({c[3]});
    }} else {{
      acc=acc-t-({c[4]});
      a=a+({c[5]});
    }}
    i=i+1;
  }}
  return a+acc-b;
}}
"""


def cases():
    return [(family,idx,source_for(family,idx))
            for family in FAMILIES for idx in range(CASES_PER_FAMILY)]


def command(args,timeout=55):
    p=subprocess.run([str(v) for v in args],capture_output=True,text=True,
                     timeout=timeout,check=False)
    if p.returncode:
        raise RuntimeError(f"exit={p.returncode} command={args!r}\n"
                           f"stderr={p.stderr[-2300:]}\nstdout={p.stdout[-600:]}")
    return p.stdout.strip()


def syntax_check():
    for family,idx,body in cases():
        p=subprocess.run(["gcc","-O2","-std=c11","-fwrapv","-Wall",
                          "-Wextra","-Werror","-fsyntax-only","-x","c","-"],
                         input=body,capture_output=True,text=True,timeout=12,
                         check=False)
        if p.returncode:
            raise RuntimeError(f"invalid C11 source {family}/{idx}: {p.stderr[:1200]}")
    return len(cases())


def text_size(path):
    out=command(["objdump","-h",path])
    for line in out.splitlines():
        pieces=line.split()
        if len(pieces)>=3 and pieces[1]==".text":
            return int(pieces[2],16)
    raise RuntimeError("ELF object missing .text: "+str(path))


def validate(default,optin,out):
    default,optin=Path(default).resolve(),Path(optin).resolve()
    out=Path(out).resolve()
    if not default.is_file() or not optin.is_file():
        raise ValueError("both actual release compiler paths required")
    out.parent.mkdir(parents=True,exist_ok=True)
    assert syntax_check()==48
    results=[]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp=Path(tmpdir)
        harness=tmp/"harness.c"
        harness.write_text(HOST)
        for family,index,body in cases():
            name=f"{family}_{index:02}"
            src=tmp/(name+".c")
            src.write_text(body)
            ref=tmp/(name+"-gcc.o")
            command(["gcc","-O2","-std=c11","-fwrapv","-fno-builtin",
                     "-fno-lto","-c",src,"-o",ref])
            gcc_exe=tmp/(name+"-gcc")
            command(["gcc","-O2","-no-pie",harness,ref,"-o",gcc_exe])
            expected=command([gcc_exe],timeout=20)
            variants={}
            for label,binary in (("default",default),("optin",optin)):
                obj=tmp/(name+"-"+label+".o")
                command([binary,src,"--language","mini-c","--ir","typeid",
                         "--emit","obj","-o",obj],timeout=70)
                exe=tmp/(name+"-"+label)
                command(["gcc","-O2","-no-pie",harness,obj,"-o",exe])
                actual=command([exe],timeout=20)
                if actual!=expected:
                    raise RuntimeError(f"{name}/{label} mismatch vs GCC: "
                                       f"expected={expected!r} actual={actual!r}")
                variants[label]={
                    "text_bytes":text_size(obj),
                    "object_sha256":hashlib.sha256(obj.read_bytes()).hexdigest(),
                }
            saved=variants["default"]["text_bytes"]-variants["optin"]["text_bytes"]
            results.append({
                "name":name,"family":family,"seed":SEED+index,
                "source_sha256":hashlib.sha256(body.encode()).hexdigest(),
                "default":variants["default"],"optin":variants["optin"],
                "gcc_execution_equal":True,"saved_text_bytes":saved,
            })
            print("X7_MEMCFG_CASE",name,
                  "default",variants["default"]["text_bytes"],
                  "optin",variants["optin"]["text_bytes"],
                  "saved",saved,flush=True)
    report={
        "schema_version":1,
        "kind":"x7_typeid_source_dag_memory_alias_cfg_native_gcc_differential",
        "families":list(FAMILIES),"seed_base":SEED,
        "cases_per_family":CASES_PER_FAMILY,
        "source_count":len(results),
        "source_validation":"independent GCC -std=c11 -fwrapv -Wall -Wextra -Werror",
        "native_reference":"GCC -O2 -fwrapv separate object and linked executable",
        "input_cases_per_source":len(VALUES)**2+512,
        "every_native_execution_matches_gcc":True,
        "rows":results,
        "reduced_count":sum(x["saved_text_bytes"]>0 for x in results),
        "increased_count":sum(x["saved_text_bytes"]<0 for x in results),
        "unchanged_count":sum(x["saved_text_bytes"]==0 for x in results),
        "net_saved_text_bytes":sum(x["saved_text_bytes"] for x in results),
        "feature_default_changed":False,
        "new_isa_rules_admitted":0,
    }
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("X7_MEMCFG_SUMMARY",json.dumps({
        "sources":len(results),"reduced":report["reduced_count"],
        "increased":report["increased_count"],
        "unchanged":report["unchanged_count"],
        "net_saved":report["net_saved_text_bytes"],
    },sort_keys=True),flush=True)
    if len(results)!=len(FAMILIES)*CASES_PER_FAMILY:
        raise RuntimeError("incomplete memory/CFG corpus")
    if report["increased_count"]:
        raise RuntimeError("generated DAG produced a .text increase in this corpus")
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--syntax-only",action="store_true")
    p.add_argument("--default")
    p.add_argument("--optin")
    p.add_argument("--out")
    args=p.parse_args()
    if args.syntax_only:
        print("X7_MEMCFG_SOURCE_SYNTAX_OK",syntax_check(),flush=True)
    else:
        if not all((args.default,args.optin,args.out)):
            p.error("native mode requires --default --optin --out")
        validate(args.default,args.optin,args.out)
