#!/usr/bin/env python3
"""Reproducible Mini-C CFG corpus, GCC differential oracle and AArch64 ELF census.

The old and new compilers must build exactly the SAME generated source. A
separate GCC compilation renames every generated function as reference, and
the C11 driver compares boundary/loop inputs. The report measures actual
physical AArch64 B encodings function-by-function, not inferred CFG scores.
"""
import argparse
import hashlib
import re
from pathlib import Path

# All shapes are source-valid unsigned C11, with independent observable paths,
# loop-carried values, early exits and diamonds. Keep the portable GCC version
# and both custom compiler revisions on identical input bytes.
SHAPES = [
    """for(unsigned int i=0;i<n;++i){
      if(((x+i)&1u)==0u){x=(x+AAu)^i;}
      else{x=(x^BBu)+i;}
      if(((x^seed)&2u)!=0u){x=x+CCu;}
      else{x=x-AAu;}
      if((x&4u)!=0u){x=x^DDu;}else{x=x+BBu;}
    }""",
    """for(unsigned int i=0;i<n;++i){
      for(unsigned int j=0;j<3u;++j){
        if(((x+j)&3u)==0u){x=(x+AAu)^(i+j);}
        else if(((x+i)&2u)!=0u){x=(x-BBu)+j;}
        else{x=(x^CCu)+DDu;}
      }
      if((i&1u)!=0u){x=x+BBu;}else{x=x^AAu;}
    }""",
    """for(unsigned int i=0;i<n;++i){
      if(((x+i)&7u)==0u){return x+AAu;}
      if(((x^i)&4u)!=0u){x=(x^BBu)+CCu;}
      else{x=(x+DDu)-AAu;}
      if(((x+seed)&3u)==0u){x=x^CCu;}
    }""",
    """unsigned int i=0u;
    while(i<n){
      if(((x+i)&3u)==0u){
        for(unsigned int j=0;j<2u;++j){x=(x+AAu)^(i+j);}
      }else{
        for(unsigned int j=0;j<3u;++j){x=(x^BBu)+(i+j);}
      }
      if(((i^x)&1u)!=0u){x=x+CCu;}else{x=x-DDu;}
      i=i+1u;
    }""",
    """for(unsigned int i=0;i<n;++i){
      if(((x+i)&1u)!=0u){
        if(((x^seed)&2u)!=0u){x=x+AAu;}
        else if((i&4u)!=0u){x=x^BBu;}
        else{x=x+CCu;}
      }else{
        if((x&8u)!=0u){x=x-DDu;}
        else{x=(x^AAu)+BBu;}
      }
      x=x+(i&7u);
    }""",
    """for(unsigned int i=0;i<n;++i){
      if((i&1u)==0u){
        for(unsigned int j=0;j<3u;++j){
          if((x&2u)!=0u){x=x+AAu;}else{x=x^BBu;}
        }
      }else{
        for(unsigned int j=0;j<2u;++j){
          if((x&4u)!=0u){x=x-CCu;}else{x=x+DDu;}
        }
      }
      x=(x+seed)^i;
    }""",
    """for(unsigned int i=0;i<n;++i){
      if((i&3u)==0u){x=x+AAu;}
      else if((i&3u)==1u){x=(x^BBu)+i;}
      else if((i&3u)==2u){x=x-CCu;}
      else{x=x^DDu;}
      if((x&1u)==0u){x=x+BBu;}
      else if((x&2u)==0u){x=x+CCu;}
      else{x=x+AAu;}
    }""",
    """for(unsigned int i=0;i<n;++i){
      if(((x+i)&1u)==0u){
        if((i&2u)!=0u){x=x+AAu;}else{x=x^BBu;}
      }else if(((x^seed)&2u)!=0u){
        x=x-CCu;
      }else{
        x=(x+DDu)^i;
      }
      if(((x^i)&8u)!=0u){return x+BBu;}
    }""",
]
N_GROUPS = 12
N_FUNCTIONS = N_GROUPS * len(SHAPES)


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    functions = []
    for i in range(N_FUNCTIONS):
        constants = {
            "AA": (i * 17 % 251) + 1,
            "BB": (i * 31 % 239) + 3,
            "CC": (i * 47 % 233) + 5,
            "DD": (i * 61 % 229) + 7,
        }
        body = SHAPES[i % len(SHAPES)]
        for name, number in constants.items():
            body = body.replace(name, str(number))
        functions.append(
            f"unsigned int bench_{i:03d}(unsigned int seed,unsigned int n)"
            "{\n unsigned int x=seed;\n" + body + "\n return x;\n}\n"
        )
    source = (
        "/* Deterministic C11 unsigned A64 physical CFG source census. */\n"
        + "\n".join(functions)
    )
    (output / "generated.c").write_text(source)
    aliases = "\n".join(
        f"#define bench_{i:03d} gcc_bench_{i:03d}" for i in range(N_FUNCTIONS)
    )
    (output / "gcc_reference.c").write_text(aliases + "\n" + source)
    prototypes = "\n".join(
        f"extern unsigned int bench_{i:03d}(unsigned int,unsigned int);\n"
        f"extern unsigned int gcc_bench_{i:03d}(unsigned int,unsigned int);"
        for i in range(N_FUNCTIONS)
    )
    calls = "\n".join(
        f"if(bench_{i:03d}(seed,n)!=gcc_bench_{i:03d}(seed,n)){{"
        f'fprintf(stderr,"CFG C11 mismatch bench_{i:03d} seed=%u n=%u\\n",seed,n);'
        "return 1;}" for i in range(N_FUNCTIONS)
    )
    oracle = (
        "#include <stdio.h>\n"
        f"{prototypes}\n"
        "int main(void){\n"
        " const unsigned int seeds[]={0u,1u,0xffffffffu,0x80000000u,0x12345678u};\n"
        " const unsigned int ns[]={0u,1u,2u,5u,11u,31u};\n"
        " for(unsigned int si=0;si<sizeof(seeds)/sizeof(seeds[0]);++si)\n"
        " for(unsigned int ni=0;ni<sizeof(ns)/sizeof(ns[0]);++ni){\n"
        " unsigned int seed=seeds[si],n=ns[ni];\n"
        f" {calls}\n"
        " }\n puts(\"A64_CFG_SOURCE_C11_ORACLE_OK\");return 0;}\n"
    )
    (output / "oracle.c").write_text(oracle)
    print(
        f"generated {N_FUNCTIONS} deterministic C11 functions; "
        f"source_sha256={hashlib.sha256(source.encode()).hexdigest()}"
    )


def parse_symbol_text(symbol_file: Path, rawtext: Path) -> dict:
    data = rawtext.read_bytes()
    found = {}
    rx = re.compile(
        r"^\s*\d+:\s+([0-9a-fA-F]+)\s+(\d+)\s+FUNC\s+"
        r"(?:GLOBAL|WEAK)\s+\S+\s+\d+\s+(bench_\d{3})\s*$"
    )
    for line in symbol_file.read_text().splitlines():
        m = rx.match(line)
        if not m:
            continue
        begin, size, name = int(m.group(1), 16), int(m.group(2)), m.group(3)
        if size <= 0 or begin + size > len(data) or size % 4:
            raise RuntimeError(f"bad text symbol {name}: {begin}+{size} / {len(data)}")
        words = [int.from_bytes(data[j:j + 4], "little")
                 for j in range(begin, begin + size, 4)]
        unconditional = sum((word & 0xfc000000) == 0x14000000 for word in words)
        found[name] = (size, unconditional,
                       hashlib.sha256(data[begin:begin + size]).hexdigest())
    if len(found) != N_FUNCTIONS:
        raise RuntimeError(f"found {len(found)} of {N_FUNCTIONS} function symbols")
    return found


def report(output: Path) -> None:
    old = parse_symbol_text(output / "single.sym", output / "single.text")
    new = parse_symbol_text(output / "dual.sym", output / "dual.text")
    changed, saved, added, total_bytes, total_b = [], 0, 0, 0, 0
    for name in sorted(old):
        a, b = old[name], new[name]
        total_bytes += b[0] - a[0]
        total_b += b[1] - a[1]
        if b[2] != a[2]:
            changed.append((name, b[0] - a[0], b[1] - a[1], a[1], b[1]))
        if b[1] < a[1]:
            saved += a[1] - b[1]
        if b[1] > a[1]:
            added += b[1] - a[1]
    print(f"TOTAL corpus={N_FUNCTIONS} changed_functions={len(changed)} "
          f"text_delta_bytes={total_bytes} B_delta={total_b} "
          f"B_removed={saved} B_added={added}")
    for name, delta, bd, old_b, new_b in changed:
        print(f"CHANGED {name} text_delta={delta} B_delta={bd} "
              f"single_B={old_b} dual_B={new_b}")
    (output / "changed.txt").write_text("\n".join(
        f"{name},{delta},{bd},{old_b},{new_b}"
        for name, delta, bd, old_b, new_b in changed
    ) + "\n")
    if added:
        raise RuntimeError("Dual trace increases actual unconditional B on source corpus")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "report"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.output)
    else:
        report(args.output)
