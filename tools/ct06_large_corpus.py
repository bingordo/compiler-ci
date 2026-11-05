#!/usr/bin/env python3
"""Generate and strictly validate CT-06 larger *real Mini-C* source cases.

Reuses the exact c_source() from the pinned compiler checkout, rather than
reimplementing a language frontend or injecting optimizer-only SSA.
The default 8/24/48 corpus and its existing nine-case validation are
left unchanged. This isolated extension accepts only 24/48/96/128.
"""
import argparse
import csv
import hashlib
from pathlib import Path
import sys

SCALES = (24, 48, 96, 128)
CASES = ("branch_chain", "array_loop", "call_graph")
FIELDS = ("language", "case", "scale", "functions", "source_bytes", "sha256", "path")


def import_generator(helpers: Path):
    if not (helpers / "source_compile_corpus.py").is_file():
        raise ValueError(f"missing compiler corpus generator: {helpers}")
    sys.path.insert(0, str(helpers.resolve()))
    from source_compile_corpus import c_source
    return c_source


def expected_manifest(root: Path, c_source):
    entries = []
    for case in CASES:
        for scale in SCALES:
            relative = Path("mini_c") / f"{case}_{scale}.c"
            source = c_source(case, scale)
            contents = source.encode("utf-8")
            entries.append({
                "language": "mini_c",
                "case": case,
                "scale": scale,
                "functions": scale + 1 if case == "call_graph" else 1,
                "source_bytes": len(contents),
                "sha256": hashlib.sha256(contents).hexdigest(),
                "path": relative.as_posix(),
            })
    return entries


def generate(root: Path, helpers: Path):
    c_source = import_generator(helpers)
    expected = expected_manifest(root, c_source)
    root.mkdir(parents=True, exist_ok=True)
    for entry in expected:
        relative = Path(entry["path"])
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = c_source(entry["case"], entry["scale"]).encode("utf-8")
        target.write_bytes(contents)
    with (root / "manifest.csv").open("w", encoding="utf-8", newline="") as fd:
        writer = csv.DictWriter(fd, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(expected)
    read_manifest(root / "manifest.csv", root, helpers)
    return expected


def read_manifest(manifest: Path, root: Path, helpers: Path):
    """Return entries expected by ct06_abba, rejecting changed or missing sources."""
    c_source = import_generator(helpers)
    expected = expected_manifest(root, c_source)
    normalized = {
        (item["case"], item["scale"]): {key: str(value) for key, value in item.items()}
        for item in expected
    }
    with manifest.open("r", encoding="utf-8", newline="") as fd:
        reader = csv.DictReader(fd)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"unexpected extended manifest schema: {reader.fieldnames}")
        entries = list(reader)
    if len(entries) != len(expected):
        raise ValueError(f"expected {len(expected)} Mini-C cases, got {len(entries)}")
    seen = set()
    for entry in entries:
        if entry["language"] != "mini_c":
            raise ValueError("not a Mini-C benchmark case")
        try:
            key = (entry["case"], int(entry["scale"]))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid extended manifest case/scale") from error
        if key in seen or key not in normalized:
            raise ValueError(f"duplicate or unexpected extended source: {key}")
        seen.add(key)
        if entry != normalized[key]:
            raise ValueError(f"manifest metadata differs from exact generator: {key}")
        source = (root / entry["path"]).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise ValueError(f"manifest path escapes or is missing: {key}")
        contents = source.read_bytes()
        if (len(contents) != int(entry["source_bytes"])
                or hashlib.sha256(contents).hexdigest() != entry["sha256"]):
            raise ValueError(f"source hash/bytes differ from generator: {key}")
    if seen != set(normalized):
        raise ValueError("missing source cases from extended corpus")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--helpers", required=True, type=Path,
                        help="Exact compiler checkout's tools directory")
    args = parser.parse_args()
    cases = generate(args.out, args.helpers)
    print(f"Generated and verified {len(cases)} real Mini-C cases: "
          f"{','.join(map(str, SCALES))} steps across {len(CASES)} families")


if __name__ == "__main__":
    main()
