#!/usr/bin/env python3
"""Generate small original ABI probes, separate from unmodified GCC tests.

The GCC compatibility families combine returns with aggregate arguments,
varargs and global data. These probes isolate return transport without
those prerequisites. All expected values are checked by C code and the
same mixed-object runner, not by a frontend-specific test implementation.
"""
from pathlib import Path


def cases():
    """Describe natural integer, SSE, mixed and memory-return layouts."""
    for size in range(1, 34):
        yield (f"bytes-{size}", "unsigned char", size, None)
    for name, field, count, second in [
        ("gp-two", "long", 2, None),
        ("sse-float", "float", 1, None),
        ("sse-float-pair", "float", 2, None),
        ("sse-double", "double", 1, None),
        ("sse-double-pair", "double", 2, None),
        ("gp-sse", "long", 1, "double"),
        ("sse-gp", "double", 1, "long"),
        ("memory-mixed", "double", 2, "long"),
    ]:
        yield name, field, count, second


def generate(root: Path):
    """Emit one independent caller, callee and main translation unit per layout."""
    root.mkdir(parents=True, exist_ok=True)
    for name, field, count, second in cases():
        stem = "probe-return-" + name
        fields = [(field, f"v{i}") for i in range(count)]
        if second:
            fields.append((second, "tail"))
        decl = "struct Result { " + " ".join(f"{ty} {member};" for ty, member in fields) + " };\n"
        prototype = "struct Result make_result(int seed);\n"
        assignments = " ".join(f"r.{member} = seed + {i};"
                               for i, (_, member) in enumerate(fields))
        checks = " || ".join(f"a.{member} != {7+i} || b.{member} != {29+i}"
                             for i, (_, member) in enumerate(fields))
        (root / f"{stem}_main.c").write_text(
            "int check_result(void); int main(void) { return check_result(); }\n")
        (root / f"{stem}_x.c").write_text(
            decl + prototype + "int check_result(void) { struct Result a = make_result(7); "
            "struct Result b = make_result(29); return " + checks + "; }\n")
        (root / f"{stem}_y.c").write_text(
            decl + "struct Result make_result(int seed) { struct Result r; "
            + assignments + " return r; }\n")

    # Preserve the original long-expression probes unchanged. Isolate
    # transport for large objects using independent short checks so
    # expression recursion is not a prerequisite to testing the ABI.
    for size in range(30, 34):
        stem = f"probe-return-bytes-{size}"
        isolated = f"probe-return-short-checks-{size}"
        for role in ("main", "y"):
            (root / f"{isolated}_{role}.c").write_text(
                (root / f"{stem}_{role}.c").read_text())
        fields = [f"v{i}" for i in range(size)]
        decl = "struct Result { " + " ".join(f"unsigned char {m};" for m in fields) + " };\n"
        checks = " ".join(f"if(a.{m} != {7+i} || b.{m} != {29+i}) return 1;"
                          for i, m in enumerate(fields))
        (root / f"{isolated}_x.c").write_text(
            decl + "struct Result make_result(int seed);\n"
            "int check_result(void) { struct Result a = make_result(7); "
            "struct Result b = make_result(29); " + checks + " return 0; }\n")
