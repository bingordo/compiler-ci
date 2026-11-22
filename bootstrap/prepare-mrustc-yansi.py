#!/usr/bin/env python3
"""Avoid expanding disabled yansi 1.0.1 detection macros in mrustc.

The current source-pinned driver only enables yansi features std and alloc,
not detect-tty or detect-env. The upstream macros already emit cfg-gated
definitions, but mrustc cannot parse their unused invocation grammar.
Adding the same cfg to the invocations is semantically identical in Rust,
and affects only the temporary runner-side vendor source.
"""
from pathlib import Path
import re
import sys
import tomllib

vendor = Path(sys.argv[1]).resolve()
path = vendor / "yansi-1.0.1/src/condition.rs"
manifest = vendor / "yansi-1.0.1/Cargo.toml"
assert path.is_file() and manifest.is_file(), "pinned vendored yansi 1.0.1 is required"
with manifest.open("rb") as file:
    spec = tomllib.load(file)["package"]
assert spec["name"] == "yansi" and spec["version"] == "1.0.1", spec
source = path.read_text()
anchors = {
    '    conditions! { feature = "detect-tty"':
        '    #[cfg(feature = "detect-tty")]\n    conditions! { feature = "detect-tty"',
    '    conditions! { feature = "detect-env"':
        '    #[cfg(feature = "detect-env")]\n    conditions! { feature = "detect-env"',
    '    conditions! { all(feature = "detect-env", feature = "detect-tty")':
        '    #[cfg(all(feature = "detect-env", feature = "detect-tty"))]\n    conditions! { all(feature = "detect-env", feature = "detect-tty")',
}
for before, after in anchors.items():
    assert source.count(before) == 1, f"unexpected original yansi source: {before}"
    source = source.replace(before, after)
assert source.count("    conditions! {") == 3
assert len(re.findall(r"    #\[cfg\(", source)) >= 3
path.write_text(source)
print("YANSI_MRUSTC_COMPAT exact_version=1.0.1 cfg_only=3 source_vendor_only=true")
