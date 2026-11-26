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
        # Emit only enumerated syntax tokens, never source identifiers or literals.
        allowed = {";",",","(",")","{","}","[","]","*","=","...",":","__attribute__","__aligned__","__alignof__","__extension__","__asm__","__typeof__","typeof","_Atomic","_Alignas","_Alignof","_Static_assert","__atomic_load_n","__atomic_store_n","__int128","__float128","__builtin_va_list","__restrict","__restrict__","volatile","const","struct","union","enum","typedef","inline","int","char","void","long","short","unsigned","signed","float","double"}
        m = re.search(r"expected '([^'\n]*)', found '([^'\n]*)'", msg)
        if m:
            print("TOKEN_EXPECTED="+(m[1] if m[1] in allowed else "other"),flush=True)
            print("TOKEN_FOUND="+(m[2] if m[2] in allowed else "other"),flush=True)
        elif "found end of input" in msg:
            print("TOKEN_FOUND=eof",flush=True)
        # Public header families and fixed diagnostic phrases only.
        for tag,pattern in {"stdatomic":r"\bstdatomic\.h\b","stddef":r"\bstddef\.h\b","stdint":r"\bstdint\.h\b","stdlib":r"\bstdlib\.h\b","atomic_type":r"atomic type|_Atomic","gnu_attribute":r"__attribute__|__aligned__","unsupported_type":r"unsupported (?:source |scalar |C )?type|type[^\n]{0,40}unsupported","unsupported_builtin":r"unsupported (?:compiler )?builtin","unsupported_declaration":r"unsupported declaration","unsupported_argument":r"unsupported (?:call )?argument","unsupported_parameter":r"unsupported (?:formal )?parameter","unsupported_global":r"unsupported global","unsupported_record":r"unsupported (?:record|aggregate)","unsupported_op":r"unsupported (?:operator|opcode|operation)","unknown_type":r"unknown type|unknown typedef"}.items():
            if re.search(pattern,msg,re.I):print("DIAG_TAG="+tag,flush=True)
        # Report fixed parser error families without copying diagnostic text,
        # source paths, or identifiers from private translation units.
        for tag, pattern in {
            "expected_type": r"expected (?:a )?type specifier|expected (?:a )?type name",
            "expected_declaration": r"expected (?:a )?declaration",
            "expected_expression": r"expected (?:an )?expression",
            "unsupported_storage": r"unsupported storage class",
            "unterminated": r"unterminated",
            "unsupported_attribute": r"unsupported GNU .*attribute",
            "unsupported_type_specifier": r"unsupported type specifier",
        }.items():
            if re.search(pattern, msg, re.I):
                print("SYNTAX_TAG="+tag, flush=True)
        # Only known public system header names, with no private paths.
        for header in ("stdio.h","string.h","stdbool.h","assert.h","limits.h",
                       "errno.h","setjmp.h","math.h","unistd.h","time.h","stdarg.h",
                       "sys/types.h","sys/stat.h","bits/types.h","bits/floatn.h"):
            if re.search(r"(?:/|\\b)"+re.escape(header)+r":\d+", msg):
                print("PUBLIC_DIAG_HEADER="+header, flush=True)
        # For Mini-C's first source-location error, privately inspect the
        # diagnostic line in the original source. Export only counts of
        # whitelisted C/GNU constructs, never source text or identifiers.
        if mode == "minic":
            locations = re.findall(r"(?m)^(.+?):(\d+)(?::\d+)?: error\[S[0-9]{4}\]", msg)
            if locations:
                file, line = locations[0]
                try:
                    p = Path(file).resolve()
                    same_source = p == src.resolve()
                    print("SYNTAX_SOURCE="+("generated" if same_source else "other"), flush=True)
                    if same_source:
                        import itertools
                        n = int(line)
                        with p.open(errors="replace") as stream:
                            window = "".join(itertools.islice(stream, max(0,n-3), n+2))
                        keywords = ("__int128","__uint128_t","__int128_t","__attribute__",
                                    "__extension__","__typeof__","typeof","__auto_type",
                                    "_Static_assert","_Alignas","_Alignof","__asm__",
                                    "__builtin_va_list","__float128","_Float128",
                                    "inline","__inline__","__inline","restrict",
                                    "__restrict__","_Atomic","typedef","struct","union",
                                    "enum","static","extern","register","const","volatile")
                        # Reproduce the exact preprocessed parser error with
                        # raw input so the diagnostic supplies a column.
                        pre = tmp / "private-preprocessed.c"
                        with pre.open("wb") as dst, (tmp/"private-cpp.log").open("wb") as err:
                            cpp = subprocess.run(["cc","-E","-P",str(src)],
                                                 stdout=dst,stderr=err,timeout=180)
                        if cpp.returncode == 0:
                            rc2,_ = attempt([driver,"--cpp=none","--emit-obj",str(tmp/"private-raw.o"),str(pre)],
                                            tmp/"private-raw.log",180)
                            raw = (tmp/"private-raw.log").read_text(errors="replace")
                            match = re.search(r":(\d+):(\d+): error\[S[0-9]{4}\]",raw)
                            print("RAW_REPLAY_EXIT="+str(rc2),flush=True)
                            print("RAW_REPLAY_LOCATED="+str(int(match is not None)),flush=True)
                            if match:
                                line2,col2 = map(int,match.groups())
                                with pre.open(errors="replace") as stream:
                                    row = next(itertools.islice(stream,line2-1,line2),"")
                                # Tokenize only the failing line and classify
                                # tokens by a fixed public allowlist; no names
                                # or literals from the source are emitted.
                                vocabulary = set(keywords) | {"__attribute","__forceinline","__declspec","__const","__const__","__volatile__","always_inline","noinline","unused","noreturn","packed","aligned","int","char","long","short","unsigned","signed","float","double","void","_Bool","(",")",";",",","*","=","{","}","[","]"}
                                tokens = list(re.finditer(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]",row))
                                k = next((i for i,t in enumerate(tokens) if t.end() >= col2),None)
                                if k is not None:
                                    def classify(t):
                                        return t.group() if t.group() in vocabulary else "other"
                                    print("SYNTAX_TOKEN="+classify(tokens[k]),flush=True)
                                    print("SYNTAX_SEQUENCE="+",".join(classify(t) for t in tokens[max(0,k-3):k+5]),flush=True)
                        for word in keywords:
                            if re.search(r"\b"+re.escape(word)+r"\b", window):
                                print("SYNTAX_NEAR="+word, flush=True)
                except (OSError, ValueError):
                    print("SYNTAX_SOURCE=unavailable", flush=True)
        codes = sorted(set(re.findall(r"error\[([PLST][0-9]{4})\]", msg)))
        print(f"CLIF_MODE={mode} SAMPLE={i} BYTES={src.stat().st_size} GCC_EXIT={gcc} CLIF_EXIT={rc} ELF={int(elf)} SECONDS={elapsed}", flush=True)
        print("CLIF_DIAG_IDS="+",".join(codes), flush=True)
        print("CLIF_DIAG_CATEGORIES="+",".join(k for k,v in categories.items() if re.search(v,msg,re.I)), flush=True)
        failures += int(gcc != 0 or rc != 0 or not elf)
    raise SystemExit(int(failures != 0))
