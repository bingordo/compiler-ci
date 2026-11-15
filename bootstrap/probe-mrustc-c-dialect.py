#!/usr/bin/env python3
"""Summarize the actual mrustc-emitted C dialect, then choose bounded Mini-C probes.

Generated compiler sources stay on the runner. Only statistics and file names
are printed. This is diagnostic coverage, not a C conformance assertion.
"""
from collections import Counter
from pathlib import Path
import re
import sys

output = Path(sys.argv[1])
selection_file = Path(sys.argv[2])
sources = sorted(output.rglob("*.c"))
if not sources:
    raise SystemExit(f"No generated C files beneath {output}")
patterns = {
    "128-bit integers": r"\b(?:unsigned\s+)?__int128\b",
    "GNU attributes": r"__attribute__\s*\(",
    "GNU inline assembly": r"\b(?:__asm__|asm)\s*(?:volatile\s*)?\(",
    "GNU builtins": r"__builtin_\w+",
    # Keep the qualifier separate from atomic_* API identifiers. The
    # previous combined count did not establish actual _Atomic use.
    "C11 _Atomic keyword": r"\b_Atomic\b",
    "C11 atomic API identifiers": r"\batomic_[A-Za-z_]\w*\b",
    "GNU atomic builtin identifiers": r"\b(?:__atomic_|__sync_)[A-Za-z_]\w*\b",
    "setjmp/longjmp": r"\b(?:setjmp|longjmp|sigsetjmp|siglongjmp)\s*\(",
    "goto": r"\bgoto\s+\w+\s*;",
    "switch": r"\bswitch\s*\(",
    "union": r"\bunion\b",
    "struct": r"\bstruct\b",
    "volatile": r"\bvolatile\b",
    "thread-local storage": r"\b(?:__thread|_Thread_local)\b",
    "compound literals": r"\(\s*(?:struct|union|[a-zA-Z_]\w*)\s*\)\s*\{",
    "GNU statement expression": r"\(\s*\{",
}
totals = Counter()
files = Counter()
sizes = {}
atomic_per_file = {}
for path in sources:
    data = path.read_text(errors="replace")
    sizes[path] = len(data)
    atomic_per_file[path] = {
        "atomic_qualifier": len(re.findall(patterns["C11 _Atomic keyword"], data)),
        "atomic_api": len(re.findall(patterns["C11 atomic API identifiers"], data)),
        "gnu_builtin": len(re.findall(patterns["GNU atomic builtin identifiers"], data)),
    }
    for feature, expression in patterns.items():
        count = len(re.findall(expression, data))
        if count:
            files[feature] += 1
            totals[feature] += count

print(f"C_DIALECT generated_units={len(sources)} generated_bytes={sum(sizes.values())}")
for feature in patterns:
    print(f"C_DIALECT {feature}: files={files[feature]} matches={totals[feature]}")

# Pick one real generated C unit per compiler crate family if possible,
# otherwise pick small files as fallback. Avoid huge inputs in this smoke.
selected = []
for label in ("compiler_ir", "compiler_opt", "compiler_backend_x86", "compiler"):
    matches = [p for p in sources if label in p.stem.lower().replace("-", "_")]
    if label == "compiler":
        matches = [p for p in matches if "libcompiler_" not in p.name.lower()]
    matches.sort(key=lambda p: (sizes[p], str(p)))
    if matches:
        candidate = matches[0]
        if candidate not in selected:
            selected.append(candidate)
if not selected:
    selected = sorted(sources, key=lambda p: (sizes[p], str(p)))[:3]
selection_file.write_text("".join(str(p) + "\n" for p in selected[:4]))
for p in selected[:4]:
    print(f"C_SAMPLE path={p} bytes={sizes[p]}")
    counts = atomic_per_file[p]
    # This is a conservative raw-source inventory. Macro expansion and
    # string/comment contexts may affect whether any construct is active.
    print(f"C_SAMPLE_ATOMIC_HINT name={p.name} "
          f"atomic_qualifier={counts['atomic_qualifier']} "
          f"atomic_api={counts['atomic_api']} "
          f"gnu_builtin={counts['gnu_builtin']} "
          "gcc_required_for_native_build=yes")
