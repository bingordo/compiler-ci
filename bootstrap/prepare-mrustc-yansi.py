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
# Match the original source with or without indentation. Older yansi sources
# put these macro invocations at file scope, not inside an indented block.
anchors = {
    'conditions! { feature = "detect-tty"': 'feature = "detect-tty"',
    'conditions! { feature = "detect-env"': 'feature = "detect-env"',
    'conditions! { all(feature = "detect-env", feature = "detect-tty")':
        'all(feature = "detect-env", feature = "detect-tty")',
}
for signature, predicate in anchors.items():
    pattern = r"(?m)^(?P<indent>[ \t]*)" + re.escape(signature)
    def guard(match):
        indent = match.group("indent")
        return f"{indent}#[cfg({predicate})]\n{indent}{signature}"
    source, count = re.subn(pattern, guard, source)
    assert count == 1, f"unexpected original yansi source: {signature}"
assert len(re.findall(r"(?m)^[ \t]*conditions! \{", source)) == 3
assert len(re.findall(r"(?m)^[ \t]*#\[cfg\(", source)) >= 3
path.write_text(source)
print("YANSI_MRUSTC_COMPAT exact_version=1.0.1 cfg_only=3 source_vendor_only=true")
