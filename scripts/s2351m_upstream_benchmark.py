#!/usr/bin/env python3
"""S23.51m: exact unmodified independent upstream native-C compilation comparison.

Benchmarks real prebuilt Rust compiler processes, not cargo or parser-only
fixtures. All accepted source SHA256 values are inherited from the separate
native/GCC compatibility probe. A/B/B/A per source avoids one-way warmup bias.
"""
import hashlib
import json
import re
import statistics
import subprocess
import time
from pathlib import Path

# Strictly accepted *original upstream* source files from CI #45871055302.
SOURCES = [
    {
        "name": "zlib-adler",
        "repo": "zlib", "file": "adler32.c", "include": ".",
        "sha256": "9cd1443a24ff2a3053961695bd432035c58347386a420d3388232376ebabe211",
        "upstream_sha": "51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf",
    },
    {
        "name": "tiny-aes",
        "repo": "aes", "file": "aes.c", "include": ".",
        "sha256": "8f522e876dda3be2b3076ab738cb72b95301d88d372cac47103cd2e53f3d41f4",
        "upstream_sha": "e72b6eff0884673997d0ca6385169bbd9b31936d",
    },
]
SAMPLES = 16
FIELDS = ["user_s", "sys_s", "rss_kib", "minor_faults", "major_faults", "voluntary_cs", "involuntary_cs"]
TIME_FMT = ("user_s=%U sys_s=%S rss_kib=%M minor_faults=%R "
            "major_faults=%F voluntary_cs=%w involuntary_cs=%c exit_code=%x")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_sources(folder):
    report = []
    for source in SOURCES:
        checkout = folder / "upstream" / source["repo"]
        head = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        assert head == source["upstream_sha"], (source["name"], head)
        path = checkout / source["file"]
        assert path.is_file()
        actual = digest(path)
        assert actual == source["sha256"], (path, actual)
        report.append({**source, "source_bytes": path.stat().st_size})
        print("S2351M_ACCEPT name=" + source["name"] +
              " bytes=" + str(path.stat().st_size) +
              " upstream=" + head + " sha256=" + actual, flush=True)
    (folder / "source-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def native_cmd(root, binary, source, out):
    src = root / "upstream" / source["repo"] / source["file"]
    inc = root / "upstream" / source["repo"] / source["include"]
    return [str(Path(binary).resolve()), "--gcc-driver", "-c", "-std=gnu11",
            "-I", str(inc.resolve()), str(src.resolve()),
            "-o", str(Path(out).resolve())]


def run(cmd, timeout=60, output=False):
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError("command failed: " + repr(cmd) + "\n" +
                           p.stderr[-3000:] + p.stdout[-1200:])
    return p if output else None


def prepare(root, old_binary, new_binary):
    """Require emitted native text identity and original unmodified GCC success."""
    report = check_sources(root)
    objects = root / "objects"
    objects.mkdir(exist_ok=True)
    for source in report:
        name = source["name"]
        original = root / "upstream" / source["repo"] / source["file"]
        include = root / "upstream" / source["repo"] / source["include"]
        run(["gcc", "-std=gnu11", "-c", "-I", str(include.resolve()),
             str(original.resolve()), "-o", str(objects / (name + "-gcc.o"))])
        for variant, binary in (("before", old_binary), ("after", new_binary)):
            out = objects / (name + "-" + variant + ".o")
            run(native_cmd(root, binary, source, out))
            run(["objcopy", "--only-section=.text", "-O", "binary",
                 str(out), str(objects / (name + "-" + variant + ".text"))])
        before = objects / (name + "-before.text")
        after = objects / (name + "-after.text")
        assert before.read_bytes() == after.read_bytes(), (
            "S23.51l changed generated native machine code", name)
        print("S2351M_NATIVE_EQ name=" + name +
              " bytes=" + str(before.stat().st_size) +
              " sha256=" + digest(before), flush=True)
    return report


def benchmark(root, before_binary, after_binary):
    """Time 16 independent A/B/B/A compilation blocks per original source."""
    report = prepare(root, before_binary, after_binary)
    timed = root / "time"
    timed.mkdir(exist_ok=True)
    entries = []
    for source in report:
        name = source["name"]
        for variant, binary in (("before", before_binary), ("after", after_binary)):
            run(native_cmd(root, binary, source, root / "objects" / (name + "-" + variant + ".o")))
        for block in range(SAMPLES):
            for position, variant in enumerate(("before", "after", "after", "before"), 1):
                binary = before_binary if variant == "before" else after_binary
                out = root / "objects" / (name + "-" + variant + ".o")
                measurement = timed / (name + "-" + str(block).zfill(2) +
                                       "-" + str(position) + "-" + variant + ".txt")
                cmd = ["/usr/bin/time", "-o", str(measurement), "-f", TIME_FMT] + \
                      native_cmd(root, binary, source, out)
                start = time.perf_counter_ns()
                run(cmd)
                wall_ns = time.perf_counter_ns() - start
                words = dict(token.split("=", 1) for token in measurement.read_text().split())
                assert words["exit_code"] == "0"
                row = {"source": name, "block": block, "position": position,
                       "variant": variant, "whole_compiler_wall_ns": wall_ns}
                for field in FIELDS:
                    row[field] = float(words[field]) if field in ("user_s", "sys_s") else int(words[field])
                entries.append(row)
        print("S2351M_TIMED name=" + name +
              " records=" + str(SAMPLES * 4), flush=True)
    (root / "time-raw.json").write_text(json.dumps(entries, indent=2) + "\n")
    report = []
    for source in [x["name"] for x in SOURCES]:
        samples = [x for x in entries if x["source"] == source]
        assert len(samples) == SAMPLES * 4
        row = {"source": source, "blocks": SAMPLES}
        for field in ("whole_compiler_wall_ns", "user_s", "rss_kib", "minor_faults"):
            before = statistics.median(x[field] for x in samples if x["variant"] == "before")
            after = statistics.median(x[field] for x in samples if x["variant"] == "after")
            row[field + "_before"] = before
            row[field + "_after"] = after
            row[field + "_ratio"] = after / before if before else None
        report.append(row)
        print("S2351M_ABBA name=" + source +
              " wall_ratio=" + format(row["whole_compiler_wall_ns_ratio"], ".4f") +
              " rss_ratio=" + format(row["rss_kib_ratio"], ".4f") +
              " median_wall_before_ms=" + format(row["whole_compiler_wall_ns_before"] / 1e6, ".3f") +
              " median_wall_after_ms=" + format(row["whole_compiler_wall_ns_after"] / 1e6, ".3f"), flush=True)
    (root / "benchmark-summary.json").write_text(json.dumps(report, indent=2) + "\n")


def profile(root, old_binary, new_binary):
    """Use separate heaptrack processes; never report instrumented RSS as real RSS."""
    entries = []
    for source in SOURCES:
        name = source["name"]
        for variant, binary in (("before", old_binary), ("after", new_binary)):
            out = root / "objects" / (name + "-" + variant + ".o")
            trace_base = root / ("heaptrack_" + name + "-" + variant)
            run(["heaptrack", "--record-only", "-o", str(trace_base)] +
                native_cmd(root, binary, source, out), timeout=120)
            matched = [p for p in root.glob(trace_base.name + "*")
                       if p.is_file() and p.suffix in (".gz", ".zst")]
            assert len(matched) == 1, (name, variant, matched)
            output = run(["heaptrack_print", str(matched[0])], timeout=120, output=True).stdout
            numbers = {}
            for title, regex in [
                ("allocations", r"(?m)^calls to allocation functions:\s*(\d+)"),
                ("temporary_allocations", r"(?m)^temporary memory allocations:\s*(\d+)"),
                ("peak_heap", r"(?m)^peak heap memory consumption:\s*([^\n]+)"),
            ]:
                hit = re.search(regex, output)
                assert hit, (name, variant, title, output[-2000:])
                numbers[title] = int(hit.group(1)) if title != "peak_heap" else hit.group(1).strip()
            numbers.update({"source": name, "variant": variant})
            entries.append(numbers)
            print("S2351M_ALLOC name=" + name + " variant=" + variant +
                  " allocations=" + str(numbers["allocations"]) +
                  " temporary=" + str(numbers["temporary_allocations"]) +
                  " peak_heap=" + numbers["peak_heap"].replace(" ", "_"), flush=True)
            (root / ("heap-summary-" + name + "-" + variant + ".txt")).write_text(output[-16000:])
            matched[0].unlink()
    (root / "heap-summary.json").write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("benchmark", "profile"))
    ap.add_argument("root", type=Path)
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    args = ap.parse_args()
    if args.phase == "benchmark":
        benchmark(args.root, args.before, args.after)
    else:
        profile(args.root, args.before, args.after)
