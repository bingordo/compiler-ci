#!/usr/bin/env python3
"""One-off large Csmith compile-time comparison; preserve raw samples and inputs."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

sys.path.insert(0, str(Path("compiler/tools").resolve()))
from csmith_probe import PROFILES, verify_generator_options, run

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
driver = str(Path(sys.argv[2]).resolve())
include = sys.argv[3]
report = {"compiler_sha": subprocess.check_output(["git","-C","compiler","rev-parse","HEAD"], text=True).strip(),
          "gcc": subprocess.check_output(["gcc","--version"], text=True),
          "csmith": subprocess.check_output(["csmith","--version"], text=True),
          "host": subprocess.check_output(["lscpu"], text=True), "cases": []}

def save():
    (out / "report.json").write_text(json.dumps(report, indent=2))

def timed(command, label, limit=180):
    """Use the existing bounded subprocess runner and GNU time for CPU/RSS."""
    timing = out / (label + ".time")
    timing.unlink(missing_ok=True)
    result = run(["/usr/bin/time", "-o", str(timing), "-f", "%e %U %S %M", *command], limit)
    record = {"command": command, "status": result["status"], "exit": result["code"],
              "stderr": result["stderr"].decode(errors="replace"), "wall_ms": result["ms"]}
    if timing.exists() and result["status"] == "ok" and result["code"] == 0:
        elapsed, user, system, rss = map(float, timing.read_text().splitlines()[-1].split())
        record.update(seconds=elapsed, cpu_seconds=user+system, rss_kib=int(rss))
    return record

def success(r):
    return r["status"] == "ok" and r["exit"] == 0

help_text = subprocess.check_output(["csmith","--help"])
# Scale the established observable integer profile, preserving safe math.
base = list(PROFILES["restricted-compound"])
for opt in ("--max-funcs","--max-block-depth","--max-block-size","--max-expr-complexity"):
    i = base.index(opt)
    del base[i:i+2]
for idx, (funcs, block, depth) in enumerate([(20,8,4),(60,12,5),(120,16,5),(60,12,5),(60,12,5),(60,12,5)]):
    flags = base + ["--max-funcs",str(funcs),"--max-block-size",str(block),
                    "--max-block-depth",str(depth),"--max-expr-complexity","8"]
    verify_generator_options(help_text, tuple(flags))
    seed = 1001 + idx
    stem = f"case-{seed}"
    src, pre = out / (stem+".c"), out / (stem+".i")
    case = {"seed": seed, "flags": flags, "samples": {}}
    report["cases"].append(case)
    # Generator stdout goes to a file, not the correctness harness's small capture.
    with src.open("wb") as f:
        gen = subprocess.run(["timeout","--kill-after=5s","90s","csmith","--seed",str(seed),*flags], stdout=f)
    case["generator_exit"] = gen.returncode
    case["lines"] = sum(1 for _ in src.open("rb"))
    case["source_bytes"] = src.stat().st_size
    case["source_sha256"] = hashlib.sha256(src.read_bytes()).hexdigest()
    save()
    if gen.returncode or case["lines"] < 1000:
        continue
    prep = timed(["gcc","-E","-P","-std=c99","-I",include,str(src),"-o",str(pre)],stem+"-pre")
    case["preprocess"] = prep
    if not success(prep):
        save()
        continue
    case["preprocessed_sha256"] = hashlib.sha256(pre.read_bytes()).hexdigest()
    case["preprocessed_bytes"] = pre.stat().st_size
    labels = ["gcc-O0","gcc-O2","gcc-O3","native"]
    for round_idx in range(3):
        for label in labels[round_idx:] + labels[:round_idx]:
            obj = out / (stem+"-"+label+".o")
            obj.unlink(missing_ok=True)
            cmd = ([driver,str(pre),"--emit","obj","-o",str(obj)] if label == "native"
                   else ["gcc","-std=c99","-w","-"+label.split("-")[1],"-c",str(pre),"-o",str(obj)])
            sample = timed(cmd,stem+"-"+label+"-"+str(round_idx))
            if success(sample):
                sample["object_bytes"] = obj.stat().st_size if obj.exists() else None
            case["samples"].setdefault(label,[]).append(sample)
            print(stem,label,round_idx,sample.get("cpu_seconds"),sample["status"],sample["exit"],flush=True)
            if not success(sample) and round_idx == 0:
                print("FIRST_DIAGNOSTIC", stem, label, sample["stderr"][:4000], flush=True)
            save()
    case["medians"] = {}
    for label, samples in case["samples"].items():
        if len(samples)==3 and all(success(s) and s.get("object_bytes") for s in samples):
            case["medians"][label] = {k:statistics.median(s[k] for s in samples)
                                      for k in ("seconds","cpu_seconds","rss_kib","object_bytes")}
    # Correctness is a separate untimed check, with a bounded execution.
    case["checksums"] = {}
    for label in case["medians"]:
        obj = out / (stem+"-"+label+".o")
        exe = out / (stem+"-"+label+".bin")
        link = run(["gcc",str(obj),"-o",str(exe)],60)
        if link["status"] == "ok" and link["code"] == 0:
            result = run([str(exe)],15)
            case["checksums"][label] = {"status":result["status"],"exit":result["code"],
                                          "stdout":result["stdout"].decode(errors="replace")}
        else:
            case["checksums"][label] = {"status":"link-failed","exit":link["code"]}
    save()

print("| seed | lines | GCC O0 CPU s | GCC O2 CPU s | GCC O3 CPU s | native CPU s | native RSS KiB |")
print("|---|---:|---:|---:|---:|---:|---:|")
for c in report["cases"]:
    m=c.get("medians",{})
    vals=[m.get(k,{}).get("cpu_seconds","NA") for k in ("gcc-O0","gcc-O2","gcc-O3","native")]
    print("|", " | ".join(map(str,[c["seed"],c["lines"],*vals,m.get("native",{}).get("rss_kib","NA")])), "|")
save()

if not any("native" in c.get("medians",{}) for c in report["cases"]):
    raise SystemExit("No successful native compile-time samples")
