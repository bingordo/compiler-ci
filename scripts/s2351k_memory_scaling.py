#!/usr/bin/env python3
"""S23.51k: compare independently generated C11 compiler workloads, whole-process
memory and allocation metrics at exact pre/post hybrid producer-view SHAs.

This script deliberately never edits source under test. It reports the actual
pinned compiler process, not cargo or the GCC reference compiler, and separates
uninstrumented /usr/bin/time RSS from heaptrack instrumentation overhead.
"""
import argparse
import hashlib
import json
import re
import statistics
import subprocess
from pathlib import Path

COUNTS = (96, 384, 768)
REPEATS = 4
TIME_FMT = ("user_s=%U sys_s=%S wall_s=%e maxrss_kb=%M "
            "minor_faults=%R major_faults=%F voluntary_cs=%w "
            "involuntary_cs=%c exit_code=%x")
FIELDS = ("user_s", "sys_s", "wall_s", "maxrss_kb",
          "minor_faults", "major_faults", "voluntary_cs", "involuntary_cs")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate(root):
    """Scale the independently maintained 96-function CFG C11 source without
    changing any original function: add copies with disjoint symbol names."""
    root.mkdir(parents=True, exist_ok=True)
    seed = (root / "seed" / "generated.c").read_text()
    names = re.findall(r"\bunsigned int bench_(\d{3})\s*\(", seed)
    assert len(names) == 96 and len(set(names)) == 96, len(names)
    assert set(map(int, names)) == set(range(96))
    info = []
    for n in COUNTS:
        pieces = []
        for index in range(n // 96):
            def rename(m):
                return "bench_" + str(int(m.group(1)) + index * 96).zfill(3)
            pieces.append(re.sub(r"\bbench_(\d{3})\b", rename, seed))
        path = root / ("cfg_" + str(n) + ".c")
        path.write_text("\n".join(pieces))
        actual = re.findall(r"\bunsigned int bench_(\d+)\s*\(", path.read_text())
        assert len(actual) == n and len(set(actual)) == n
        subprocess.run(["gcc", "-std=c11", "-O2", "-c", str(path),
                        "-o", str(root / ("gcc_" + str(n) + ".o"))],
                       check=True, timeout=120)
        item = dict(source=path.name, functions=n, bytes=path.stat().st_size,
                    sha256=sha(path))
        info.append(item)
        print("S2351K_SOURCE " + " ".join(str(k) + "=" + str(v)
                                        for k, v in item.items()), flush=True)
    (root / "source-manifest.json").write_text(json.dumps(info, indent=2) + "\n")


def compiler_cmd(binary, source, out):
    return [str(Path(binary).resolve()), str(Path(source).resolve()),
            "--language", "mini-c", "--target", "x86_64",
            "--emit", "obj", "-o", str(Path(out).resolve())]


def benchmark(root, before, after):
    """Alternate old/new/new/old to limit per-revision runner drift."""
    old = Path(before).resolve()
    new = Path(after).resolve()
    assert old.is_file() and new.is_file() and old != new
    outputs = root / "native"
    outputs.mkdir(exist_ok=True)
    rows = []
    for n in COUNTS:
        source = root / ("cfg_" + str(n) + ".c")
        assert source.is_file()
        # Warm both compilers before timed measurements. A separate child process
        # is created for every timed compilation, so RSS is per invocation.
        for revision, binary in (("before", old), ("after", new)):
            out = outputs / (revision + "_" + str(n) + ".o")
            subprocess.run(compiler_cmd(binary, source, out), check=True,
                           timeout=150, capture_output=True, text=True)
        original = outputs / ("before_" + str(n) + ".o")
        trial = outputs / ("after_" + str(n) + ".o")
        for suffix, output in (("before", original), ("after", trial)):
            subprocess.run(["objcopy", "--only-section=.text", "-O", "binary",
                            str(output), str(outputs / (suffix + "_" + str(n) + ".text"))],
                           check=True)
        assert (outputs / ("before_" + str(n) + ".text")).read_bytes() == \
               (outputs / ("after_" + str(n) + ".text")).read_bytes(), \
               ("emitted native text differs", n)
        for index, revision in enumerate(("before", "after", "after", "before"), 1):
            binary = old if revision == "before" else new
            metrics = root / ("time_" + str(n) + "_" + str(index) + "_" +
                              revision + ".txt")
            out = outputs / (revision + "_" + str(n) + ".o")
            cmd = ["/usr/bin/time", "-o", str(metrics), "-f", TIME_FMT,
                   "timeout", "--signal=TERM", "--kill-after=5s", "150s"] + \
                  compiler_cmd(binary, source, out)
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=165)
            if run.returncode:
                raise RuntimeError("source compilation failed " + str((n, revision, run.returncode)) +
                                   "\n" + run.stderr[-2000:] + run.stdout[-2000:])
            line = metrics.read_text().strip()
            parts = dict(token.split("=", 1) for token in line.split())
            assert int(parts["exit_code"]) == 0
            row = dict(functions=n, revision=revision, position=index)
            for field in FIELDS:
                row[field] = float(parts[field]) if field in ("user_s", "sys_s", "wall_s") else int(parts[field])
            rows.append(row)
            print("S2351K_TIME functions=" + str(n) + " revision=" + revision +
                  " position=" + str(index) + " " + line, flush=True)
    (root / "whole-process-timings.json").write_text(json.dumps(rows, indent=2) + "\n")
    # The exact same generated output text under both compiler revisions
    # is a necessary independent acceptance gate for the storage-only change.
    print("S2351K_NATIVE_EQ all_sizes=" + ",".join(map(str, COUNTS)), flush=True)


def summarize(root):
    rows = json.loads((root / "whole-process-timings.json").read_text())
    report = dict(schema="compiler.s2351k.whole-process-memory.v1",
                  source="independent pinned 96-CFG-function generator, replicated without IR modifications",
                  order=["before", "after", "after", "before"],
                  runtime="same-run isolated compiler process, GNU time /usr/bin/time",
                  comparisons=[])
    for n in COUNTS:
        block = [row for row in rows if row["functions"] == n]
        assert [(r["revision"], r["position"]) for r in block] == [
            ("before", 1), ("after", 2), ("after", 3), ("before", 4)]
        datum = dict(functions=n, raw=block)
        for field in FIELDS:
            old = statistics.median(r[field] for r in block if r["revision"] == "before")
            new = statistics.median(r[field] for r in block if r["revision"] == "after")
            datum[field + "_baseline"] = old
            datum[field + "_candidate"] = new
            datum[field + "_ratio"] = new / old if old else None
        report["comparisons"].append(datum)
        print("S2351K_ABBA functions=" + str(n) +
              " wall_ratio=" + format(datum["wall_s_ratio"], ".4f") +
              " user_ratio=" + format(datum["user_s_ratio"], ".4f") +
              " maxrss_ratio=" + format(datum["maxrss_kb_ratio"], ".4f") +
              " minor_fault_ratio=" +
              (format(datum["minor_faults_ratio"], ".4f")
               if datum["minor_faults_ratio"] is not None else "n/a"), flush=True)
    (root / "s2351k-whole-process-report.json").write_text(json.dumps(report, indent=2) + "\n")


def heap_summary(root, before, after):
    """Profile allocation events separately, excluding tracing overhead from RSS."""
    bins = {"before": Path(before).resolve(), "after": Path(after).resolve()}
    records = []
    for n in (96, 768):
        source = root / ("cfg_" + str(n) + ".c")
        for revision, binary in bins.items():
            output = root / ("heap_" + str(n) + "_" + revision + ".o")
            destination = root / ("heaptrack_" + str(n) + "_" + revision)
            subprocess.run(["timeout", "--signal=TERM", "--kill-after=5s", "200s",
                            "heaptrack", "--record-only", "-o", str(destination)] +
                           compiler_cmd(binary, source, output),
                           check=True, timeout=215)
            matches = list(root.glob(destination.name + "*"))
            matches = [x for x in matches if x.is_file() and x.suffix in (".gz", ".zst")]
            assert len(matches) == 1, (revision, n, [str(x) for x in matches])
            trace = matches[0]
            result = subprocess.run(["heaptrack_print", str(trace)], check=True,
                                    capture_output=True, text=True, timeout=90)
            raw = result.stdout
            summary = {}
            for key, pattern in {
                "allocations": r"(?m)^calls to allocation functions:\s*(\d+)",
                "temporary_allocations": r"(?m)^temporary memory allocations:\s*(\d+)",
                "peak_heap": r"(?m)^peak heap memory consumption:\s*([^\n]+)",
                "peak_rss_instrumented": r"(?m)^peak RSS \(including heaptrack overhead\):\s*([^\n]+)",
            }.items():
                found = re.search(pattern, raw)
                assert found is not None, (key, revision, n, raw[-2200:])
                summary[key] = int(found.group(1)) if key in ("allocations", "temporary_allocations") else found.group(1).strip()
            summary.update(functions=n, revision=revision)
            records.append(summary)
            print("S2351K_ALLOC " + " ".join(str(k) + "=" + str(v).replace(" ", "_")
                                              for k, v in summary.items()), flush=True)
            # The full report is useful for hotspot inspection; the raw trace
            # can be hundreds of MB and is not needed in the CI artifact.
            (root / ("heaptrack_" + str(n) + "_" + revision + ".txt")).write_text(
                raw[-25000:])
            trace.unlink()
    (root / "s2351k-heaptrack-summary.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("generate", "benchmark", "summarize", "heap"))
    ap.add_argument("root", type=Path)
    ap.add_argument("--baseline")
    ap.add_argument("--candidate")
    args = ap.parse_args()
    if args.phase == "generate":
        generate(args.root)
    elif args.phase == "benchmark":
        assert args.baseline and args.candidate
        benchmark(args.root, args.baseline, args.candidate)
    elif args.phase == "summarize":
        summarize(args.root)
    else:
        assert args.baseline and args.candidate
        heap_summary(args.root, args.baseline, args.candidate)
