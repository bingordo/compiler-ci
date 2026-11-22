#!/usr/bin/env python3
"""Prepare exact pinned rcc sources for minicargo, in runner checkouts only.

Minicargo cannot parse git dependencies or workspace inheritance. This tool
does not stub, erase features, or modify the user's compiler/mini-c sources:
it rewrites only checked-out CI copies after verifying the exact source SHA.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tomllib

root = Path(sys.argv[1]).resolve()
mini = root / "mini-c"
rcc = root / "rcc"
REV = "aa7ba4115a986cd533965ae297de6490315c35cb"
URL = "https://github.com/noname0310/rcc.git"
assert subprocess.check_output(
    ["git", "-C", str(rcc), "rev-parse", "HEAD"], text=True
).strip() == REV, "rcc source checkout must match exact Mini-C pin"

mini_paths = [
    mini / "crates/preprocess/Cargo.toml",
    mini / "vendor/rcc_preprocess/Cargo.toml",
]
mapped = ("rcc_span", "rcc_errors", "rcc_target", "rcc_session",
          "rcc_data_structures", "rcc_lexer")
with (rcc / "Cargo.toml").open("rb") as f:
    workspace = tomllib.load(f)["workspace"]
packages = workspace["package"]
deps = workspace["dependencies"]
assert packages["edition"] == "2021" and packages["version"] == "0.0.1"

# Rewrite only known pinned source-owned rcc git dependencies in Mini-C;
# keep optional features and every other dependency exactly as source.
changed = 0
for path in mini_paths:
    with path.open("rb") as f:
        original = tomllib.load(f)["dependencies"]
    content = path.read_text()
    for name, dep in original.items():
        if not isinstance(dep, dict) or dep.get("git") != URL:
            continue
        assert name in mapped and dep.get("rev") == REV, (name, dep)
        assert set(dep) <= {"git", "rev", "optional"}, (name, dep)
        dest = rcc / "crates" / name / "Cargo.toml"
        assert dest.is_file(), (name, dest)
        relative = os.path.relpath(dest.parent, path.parent)
        # Do not remove optional flags; they encode actual C preprocessor ABI.
        optional = ", optional = true" if dep.get("optional") else ""
        regexp = rf'^(\\s*{re.escape(name)}\\s*=\\s*)\\{{\\s*git\\s*=\\s*"{re.escape(URL)}"\\s*,\\s*rev\\s*=\\s*"{REV}"\\s*{",\\s*optional\\s*=\\s*true" if optional else ""}\\s*\\}}\\s*$'
        # Use source manifest table data rather than reinterpreting git references:
        # the only accepted source spellings are these fully checked lines.
        lines = content.splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines)
                if line.lstrip().startswith(name + " =") or
                   line.lstrip().startswith(name + "  =")]
        assert len(hits) == 1, (name, len(hits))
        i = hits[0]
        assert 'git = "' + URL + '"' in lines[i] and 'rev = "' + REV + '"' in lines[i]
        lines[i] = f'{name} = {{ path = "{relative}"{optional} }}\\n'
        content = "".join(lines)
        changed += 1
    path.write_text(content)
assert changed >= 9, f"expected 9 or more source rcc Git declarations; saw {changed}"

def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
    raise ValueError(type(value))

# Flatten the source workspace metadata in only the required transitive crates.
# Minicargo parses direct Cargo.toml files; it cannot resolve workspace=true.
for name in mapped:
    path = rcc / "crates" / name / "Cargo.toml"
    with path.open("rb") as f:
        original = tomllib.load(f)
    lines = path.read_text().splitlines(keepends=True)
    out = []
    section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        pkg = re.fullmatch(r"([A-Za-z_-]+)\\.workspace\\s*=\\s*true", stripped)
        if pkg and section == "[package]":
            field = pkg.group(1)
            assert field in packages, (name, field)
            out.append(f"{field} = {toml_value(packages[field])}\\n")
            continue
        dep = re.fullmatch(r"([A-Za-z_-]+)\\s*=\\s*\\{.*workspace\\s*=\\s*true.*\\}", stripped)
        if dep and section in ("[dependencies]", "[dev-dependencies]", "[build-dependencies]"):
            dep_name = dep.group(1)
            from_manifest = original[section.strip("[]")][dep_name]
            assert from_manifest["workspace"] is True
            entry = deps[dep_name]
            entry = {"version":entry} if isinstance(entry,str) else dict(entry)
            if "path" in entry:
                entry["path"] = os.path.relpath(rcc / entry["path"], path.parent)
            entry.update({k:v for k,v in from_manifest.items() if k!="workspace"})
            out.append(f"{dep_name} = {toml_value(entry)}\\n")
            continue
        out.append(line)
    rewritten = "".join(out)
    assert ".workspace" not in rewritten and "workspace = true" not in rewritten, name
    path.write_text(rewritten)
    with path.open("rb") as f:
        checked = tomllib.load(f)
    assert checked["package"]["version"] == packages["version"], name
    assert checked["package"]["edition"] == packages["edition"], name
    assert checked["package"]["name"] == name, name

for path in mini_paths:
    with path.open("rb") as f:
        checked = tomllib.load(f)
    for name,dep in checked["dependencies"].items():
        assert not (isinstance(dep,dict) and dep.get("git")==URL), (path,name)
print(f"MRUSTC_RCC_LOCAL_SOURCE prepared={len(mapped)} pinned={REV} source_manifest_rewrites={changed}")
