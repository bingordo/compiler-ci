#!/usr/bin/env python3
"""One-time S23.51m upstream C source compatibility audit.

Source files are unchanged checked-out upstream artifacts; successful GCC
compilation is not substituted for the TypeId compiler's own native result.
Native unsupported constructs remain explicitly recorded failures.
"""
import hashlib
import json
import subprocess
from pathlib import Path

PROJECTS = {
    "zlib": ["madler/zlib", "v1.3.1"],
    "inih": ["benhoyt/inih", "r60"],
    "aes": ["kokke/tiny-AES-c", "v1.0.0"],
    "libb64": ["libb64/libb64", "master"],
    "cjson": ["DaveGamble/cJSON", "v1.7.19"],
}
SOURCES = [
    ("zlib-adler", "zlib", "adler32.c", (".",)),
    ("zlib-crc", "zlib", "crc32.c", (".",)),
    ("inih", "inih", "ini.c", (".",)),
    ("tiny-aes", "aes", "aes.c", (".",)),
    ("libb64-encode", "libb64", "src/cencode.c", ("include",)),
    ("libb64-decode", "libb64", "src/cdecode.c", ("include",)),
    ("cjson", "cjson", "cJSON.c", (".",)),
]


def command(argv, limit=160):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=limit)
        return dict(returncode=p.returncode, stdout=p.stdout[-12000:],
                    stderr=p.stderr[-20000:], timed_out=False)
    except subprocess.TimeoutExpired as ex:
        return dict(returncode=124,
                    stdout=str(ex.stdout or "")[-12000:],
                    stderr=str(ex.stderr or "")[-20000:], timed_out=True)


def probe(work, compiler):
    """Attempt only a single unchanged original source file at a time."""
    work.mkdir(parents=True, exist_ok=True)
    upstream = work / "upstream"
    objects = work / "objects"
    logs = work / "logs"
    objects.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    compiler = str(Path(compiler).resolve())
    assert Path(compiler).is_file(), compiler
    refs = {}
    for name, (repository, tag) in PROJECTS.items():
        checkout = upstream / name
        assert checkout.is_dir(), checkout
        result = command(["git", "-C", str(checkout), "rev-parse", "HEAD"])
        assert result["returncode"] == 0
        sha = result["stdout"].strip()
        assert len(sha) == 40
        refs[name] = dict(repository=repository, requested_ref=tag, actual_sha=sha)
    (work / "upstream-revisions.json").write_text(json.dumps(refs, indent=2) + "\n")

    report = []
    for name, project, filename, include_dirs in SOURCES:
        root = upstream / project
        source = root / filename
        assert source.is_file(), source
        includes = []
        for incl in include_dirs:
            folder = (root / incl).resolve()
            assert folder.is_dir(), folder
            includes.extend(["-I", str(folder)])
        output_gcc = (objects / (name + "-gcc.o")).resolve()
        output_native = (objects / (name + "-typeid.o")).resolve()
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        gcc = command(["gcc", "-std=gnu11", "-c", *includes,
                       str(source.resolve()), "-o", str(output_gcc)])
        native = command([compiler, "--gcc-driver", "-c", "-std=gnu11",
                          *includes, str(source.resolve()), "-o",
                          str(output_native)])
        log = {
            "task": name, "project": project, "source": filename,
            "source_sha256": source_sha,
            "source_bytes": source.stat().st_size,
            "repository": refs[project]["repository"],
            "requested_ref": refs[project]["requested_ref"],
            "upstream_sha": refs[project]["actual_sha"],
            "gcc_ok": gcc["returncode"] == 0,
            "typeid_ok": native["returncode"] == 0 and output_native.is_file(),
            "gcc_rc": gcc["returncode"], "typeid_rc": native["returncode"],
            "gcc_stdout": gcc["stdout"], "gcc_stderr": gcc["stderr"],
            "typeid_stdout": native["stdout"], "typeid_stderr": native["stderr"],
            "typeid_timeout": native["timed_out"],
        }
        (logs / (name + ".json")).write_text(json.dumps(log, indent=2) + "\n")
        row = {k: v for k, v in log.items()
               if k not in ("gcc_stdout", "gcc_stderr", "typeid_stdout", "typeid_stderr")}
        if row["typeid_ok"]:
            row["native_object_bytes"] = output_native.stat().st_size
            row["native_object_sha256"] = hashlib.sha256(
                output_native.read_bytes()).hexdigest()
        report.append(row)
        print("S2351M_PROBE source=" + name +
              " gcc=" + str(int(row["gcc_ok"])) +
              " typeid=" + str(int(row["typeid_ok"])) +
              " bytes=" + str(row["source_bytes"]) +
              " sha=" + source_sha +
              " checkout=" + refs[project]["actual_sha"][:12], flush=True)
    (work / "source-probe.json").write_text(json.dumps(report, indent=2) + "\n")
    accepted = [r["task"] for r in report if r["gcc_ok"] and r["typeid_ok"]]
    unsupported = [r["task"] for r in report if r["gcc_ok"] and not r["typeid_ok"]]
    print("S2351M_PROBE_DONE gcc=" + str(sum(r["gcc_ok"] for r in report)) +
          " native=" + str(len(accepted)) +
          " accepted=" + ",".join(accepted) +
          " gaps=" + ",".join(unsupported), flush=True)
    # A successful discovery run need not invent a benchmark candidate. Source
    # gaps are inventoried separately; later benchmarks choose supported files.
    assert sum(r["gcc_ok"] for r in report) >= 4, "incomplete GCC baseline"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--compiler", required=True)
    args = ap.parse_args()
    probe(args.work, args.compiler)
