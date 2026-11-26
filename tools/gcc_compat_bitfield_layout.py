"""Run original GCC struct-layout interoperability sources without rewriting.

The upstream generator emits whole translation-unit groups, not independent
single-field programs. Preserve the complete generated headers and wrappers.
Select groups containing bit-field operations or zero-width declarations.
"""
from pathlib import Path
import re
import shutil
import subprocess

from gcc_compat_inventory import inventory


def layout_directive(line, source_dir, target):
    """Interpret only the pinned generator's known directives on Linux x86-64.

    The caller supplies -I, -w, -Wno-abi and -mno-mmx to GCC. Other target
    alternatives are inapplicable on this target. Diagnostic pruning has no
    effect on the executable oracle; compiler errors are never pruned.
    """
    if not re.fullmatch(r"x86_64-[A-Za-z0-9_-]*linux[A-Za-z0-9_-]*", target):
        return False
    if line in (
        '/* { dg-require-effective-target int32plus } */',
        '/* { dg-prune-output ".*-Wno-abi.*" } */',
        '/* { dg-prune-output ".*Offset of packed bit-field.*" } */',
    ):
        return True
    for prefix in ("", "-w "):
        base = prefix + "-I" + source_dir.as_posix()
        for tail in (
            '"',
            ' -Wno-abi"',
            ' -mno-mmx -Wno-abi" { target i?86-*-* x86_64-*-* }',
            ' -fno-common" { target hppa*-*-hpux* powerpc*-*-darwin* }',
            ' -mno-mmx -fno-common -Wno-abi" { target i?86-*-darwin* x86_64-*-darwin* }',
            ' -mno-base-addresses" { target mmix-*-* }',
            ' -mlongcalls -mtext-section-literals" { target xtensa*-*-* }',
        ):
            if line == '/* { dg-options "' + base + tail + ' } */':
                return True
    return False


def select_batches(names, limit):
    """Choose evenly across the deterministic upstream corpus; zero means all."""
    if limit < 0:
        raise ValueError("layout batch limit must be nonnegative")
    if not limit or limit >= len(names):
        return list(names)
    if limit == 1:
        return [names[0]]
    return [names[i * (len(names) - 1) // (limit - 1)] for i in range(limit)]


def generate_layout(root, work, cc, limit):
    """Build the pinned upstream generator, run its default 3000 layouts,
    retain all source artifacts, and return the selected unmodified triads.
    """
    dest = work / "gcc-struct-layout"
    dest.mkdir(parents=True, exist_ok=False)
    generator = work / "gcc-struct-layout-generator"
    subprocess.run([cc, "-std=gnu11", "-O2", "-o", str(generator),
                    *[str(root / p) for p in ("struct-layout-1_generate.c",
                        "generate-random.c", "generate-random_r.c")]],
                   check=True, timeout=120)
    subprocess.run([str(generator), "-s", str(root), "-d", str(dest)],
                   check=True, timeout=120)
    # EDG reads originals from this directory, so reproduce the upstream
    # include search using unchanged copies of all compatibility headers.
    for header in root.glob("*.h"):
        shutil.copyfile(header, dest / header.name)
    groups = inventory(dest, "t")
    candidates = []
    details = []
    for case in groups:
        name = case["case"]
        header = dest / (name + "_test.h")
        if case["missing_roles"] or not header.is_file():
            raise ValueError("incomplete upstream generated layout batch: " + name)
        source = header.read_text()
        bitfields = len(re.findall(r"\bB\s*\(", source))
        zero_width = len(re.findall(r":\s*0\s*;", source))
        if bitfields or zero_width:
            candidates.append(case)
            details.append({"case": name, "bitfield_operations": bitfields,
                            "zero_width_declarations": zero_width})
    names = [case["case"] for case in candidates]
    chosen = set(select_batches(names, limit))
    if not chosen:
        raise ValueError("upstream generator produced no bit-field layouts")
    selected = []
    for case in candidates:
        if case["case"] in chosen:
            case["layout_directive_source"] = str(root)
            selected.append(case)
    return dest, selected, {"generated_batches": len(groups),
                            "bitfield_batches": details,
                            "selected_batches": sorted(chosen)}
