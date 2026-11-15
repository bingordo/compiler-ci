#!/usr/bin/env python3
"""Prepare *temporary runner checkout only* manifests for mrustc/minicargo.

minicargo cannot parse git dependencies in Cargo.toml, or dynamically fetch
registry packages. Preserve original manifests in the source repositories.
"""
import pathlib
import subprocess
import sys
import tomllib

root = pathlib.Path(sys.argv[1]).resolve()
driver = root / "compiler/crates/driver/Cargo.toml"
with driver.open("rb") as file:
    manifest = tomllib.load(file)
assert manifest["package"]["name"] == "compiler"

frontends = (
    ("oberon-front-ir", "oberon", "oberon/oberon/crates/front-ir"),
    ("mini-c-syntax", "mini-c", "mini-c/crates/syntax"),
    ("mini-c-sema", "mini-c", "mini-c/crates/sema"),
)

source = driver.read_text()
for crate, repo, checkout_path in frontends:
    dep = manifest["dependencies"][crate]
    assert dep["git"] == f"ssh://git@github.com/bingordo/{repo}.git", (crate, dep)
    pinned = dep["rev"]
    actual = subprocess.check_output(
        ["git", "-C", str(root / repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert actual == pinned, f"{repo}: checked out {actual}, expected {pinned}"
    original = f'{crate} = {{ git = "{dep["git"]}", rev = "{pinned}" }}'
    local = f'{crate} = {{ path = "../../../{checkout_path}" }}'
    assert source.count(original) == 1, f"manifest unexpectedly changed: {crate}"
    source = source.replace(original, local)
driver.write_text(source)

# Both frontends inherit one dependency from their own Cargo workspace.
# Replace only that inheritance: it is a normal registry crate that minicargo
# will resolve from the locally vendored copy, not a mock or stub.
for manifest_path, workspace_path in (
    ("oberon/oberon/crates/syntax/Cargo.toml", "oberon/oberon/Cargo.toml"),
    ("mini-c/crates/syntax/Cargo.toml", "mini-c/Cargo.toml"),
):
    with (root / workspace_path).open("rb") as file:
        workspace = tomllib.load(file)
    dep = workspace["workspace"]["dependencies"]["lalrpop-util"]
    assert dep["version"] == "=0.23.1" and dep["default-features"] is False
    path = root / manifest_path
    original = "lalrpop-util = { workspace = true }"
    content = path.read_text()
    assert content.count(original) == 1, f"unexpected syntax manifest: {manifest_path}"
    path.write_text(content.replace(
        original, 'lalrpop-util = { version = "=0.23.1", default-features = false }'
    ))

with driver.open("rb") as file:
    revised = tomllib.load(file)
assert all("path" in revised["dependencies"][crate] for crate, _, _ in frontends)
print("Driver probe manifests prepared with pinned local frontends and LALRPOP runtime.")
