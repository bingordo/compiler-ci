#!/usr/bin/env bash
# Diagnostic, non-gating EDG parse / TypeId lowering audit of mrustc-generated C.
# Runs inside the pinned EDG CI container; private generated C remains local.
set -euo pipefail
cd /workspace
export PATH="$HOME/.cargo/bin:$PATH"
export EDG_BASE=/workspace/edgcpp/bases/docker/dev-env/gcc
export EDGCPP_SOURCE_DIR=/workspace/edgcpp
export EDGCPP_COMPILE_COMMANDS=/workspace/edgcpp/build/gcc-release/compile_commands.json
export EDGCPP_LIB_DIR=/workspace/edgcpp/build/gcc-release/lib
export EDGCPP_EDG_REVISION=b195677be8b9b56c8337afad6be8064d21a54082

# The GitHub runner owns these bind-mounted checkouts, whereas this pinned
# EDG container may run as a different UID. Trust ONLY the three explicitly
# mounted checkout directories needed by this probe, not every repository.
# Git 2.35+ otherwise rejects rev-parse with "dubious ownership" before EDG
# has a chance to parse any generated C.
for checkout in /workspace/edgcpp /workspace/edgcpp-rust /workspace/compiler; do
  test -d "$checkout/.git"
  git config --global --add safe.directory "$checkout"
done

test "$(git -C edgcpp rev-parse HEAD)" = "$EDGCPP_EDG_REVISION"
test "$(git -C edgcpp-rust rev-parse HEAD)" = 6bcf1508555fcc0c892c1e8e00929dda26154931
test -f "$AUDIT_SOURCE"
if ! command -v cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
    sh -s -- -y --profile minimal --default-toolchain 1.99.1
fi
rustup toolchain install 1.99.1 --profile minimal
rustup default 1.99.1

cd edgcpp
cmake --preset linux-gcc-release \
  -DEDG_BUILD_LIB=OFF -DEDG_BUILD_CALLABLE_LIBRARY=ON \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build/gcc-release --target error-info --parallel 2
mkdir -p build/gcc-release/lib build/prebuilt-edg
curl --fail --location --silent --show-error --retry 4 --retry-delay 2 \
  "https://github.com/bingundu/edgcpp/releases/download/edgcpfe-v2026.11.14-upstream-177765bb7731/edgcpfe-ci-upstream-callable-edg-release-20261114-linux-x86_64.tar.gz" \
  -o build/prebuilt-edg/archive.tar.gz
printf '%s  %s\n' \
  1ce495b629924f31382ecccc891a7a72caae1014ddfa2ba7dd39b44d8b0a9526 \
  build/prebuilt-edg/archive.tar.gz | sha256sum --check
tar -xzf build/prebuilt-edg/archive.tar.gz -C build/prebuilt-edg
grep -Fx "commit=$EDGCPP_EDG_REVISION" build/prebuilt-edg/BUILD-INFO.txt
test -s build/prebuilt-edg/lib/libedgcpfe.a
cp build/prebuilt-edg/lib/libedgcpfe.a build/gcc-release/lib/libedgcpfe.a
source build/gcc-release/environment.sh
cd /workspace

# Match the pinned EDG CI's GNU dialect configuration.
version="$(gcc -dumpfullversion -dumpversion)"
IFS=. read -r major minor patch <<< "$version"
gnu_number=$((10#${major}*10000+10#${minor:-0}*100+10#${patch:-0}))
echo "EDG_C_AUDIT source=$AUDIT_SOURCE gnu_version=$gnu_number"
# The bridge's native driver discovers GCC include paths; explicitly supply
# the same host built-in/system directories to the parse-only EDG example.
mapfile -t gcc_includes < <(
  gcc -E -x c /dev/null -v 2>&1 |
    awk '/#include <...> search starts here:/{active=1;next} /^End of search list/{active=0} active && /^ /{sub(/^[[:space:]]+/, "");print}'
)
include_args=()
for dir in "${gcc_includes[@]}"; do
  if [[ -d "$dir" ]]; then include_args+=("-I$dir"); fi
done
echo "EDG_C_AUDIT builtin_include_dirs=${#include_args[@]}"
# The public EDG parse example dumps every node and should not be used
# with 50+ MiB of private generated source. Build a pinned bridge with a
# separate tiny public compiler-ci entry point that emits bounded counters.
test ! -e edgcpp-rust/examples/ci_edg_parse_probe.rs
cp bootstrap/edg_parse_probe.rs edgcpp-rust/examples/ci_edg_parse_probe.rs
timeout --signal=TERM --kill-after=10s 20m cargo build --quiet \
  --manifest-path edgcpp-rust/Cargo.toml --example ci_edg_parse_probe
parser=/workspace/edgcpp-rust/target/debug/examples/ci_edg_parse_probe
test -x "$parser"
ulimit -c 0

# Baseline: these public C fixtures independently isolate GNU layout,
# linkage, calling-convention, and naked attributes. GCC is the dialect
# oracle; EDG acceptance is a different, parser-only result.
for kind in alignment linkage abi naked control_globals weak_function section_function control_asm aligned_global scalar_global weak_scalar_global; do
  fixture="/workspace/bootstrap/fixtures/edg-gnu-attributes/${kind}.c"
  test -s "$fixture"
  gcc -std=gnu11 -fsyntax-only "$fixture"
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 45s \
      "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$fixture"
  ) > "/audit-tmp/edg-gnu-${kind}.log" 2>&1
  fixture_rc=$?
  set -e
  echo "EDG_GNU_ATTR_PARSE_${kind^^}_EXIT=$fixture_rc"
  # Fixture source is public, but preserve the bounded log policy anyway.
  grep -m 1 '^EDG_PROBE_RESULT ' "/audit-tmp/edg-gnu-${kind}.log" || true
done

# Probe a SMALL real generated TU first; previously we attempted the 53.6
# MiB compiler-ir output and EDG segfaulted (139) before TypeId lowering.
# Keep private source lines, symbols and EDG's enormous raw IL dump off logs.
echo "EDG_C_SAMPLE_BYTES=$(stat -c %s "$AUDIT_SOURCE")"
set +e
(
  cd /workspace/edgcpp/build/gcc-release
  timeout --signal=TERM --kill-after=10s 90s \
    "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$AUDIT_SOURCE"
) > /audit-tmp/edg-generated-c-parse.log 2>&1
parse_rc=$?
set -e
echo "EDG_C_PARSE_EXIT=$parse_rc"
grep -m 1 '^EDG_PROBE_RESULT ' /audit-tmp/edg-generated-c-parse.log || true
if (( parse_rc >= 128 )); then
  echo "EDG_C_PARSE_CLASS=process_signal_or_crash"
elif (( parse_rc != 0 )); then
  echo "EDG_C_PARSE_CLASS=frontend_or_bridge_rejection"
else
  echo "EDG_C_PARSE_CLASS=accepted"
fi

# Keep the previous large-input crash as an independently reported scaling
# gate, separate from any successful small-input syntax/ABI result.
if [[ -n "${AUDIT_LARGE_SOURCE:-}" && -s "$AUDIT_LARGE_SOURCE" &&
      "$AUDIT_LARGE_SOURCE" != "$AUDIT_SOURCE" ]]; then
  echo "EDG_C_LARGE_BYTES=$(stat -c %s "$AUDIT_LARGE_SOURCE")"
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 90s \
      "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$AUDIT_LARGE_SOURCE"
  ) > /audit-tmp/edg-large-c-parse.log 2>&1
  large_rc=$?
  set -e
  echo "EDG_C_LARGE_PARSE_EXIT=$large_rc"
  grep -m 1 '^EDG_PROBE_RESULT ' /audit-tmp/edg-large-c-parse.log || true
fi

# Frontend acceptance alone proves nothing about attribute-aware ELF.
# Test the exact public fixture inputs through the production native TypeId
# driver even if the real C parser rejected its larger workload.
timeout --signal=TERM --kill-after=10s 20m cargo build --quiet \
  --manifest-path compiler/tools/edg-driver/Cargo.toml
native=/workspace/compiler/tools/edg-driver/target/debug/compiler-edg-driver
test -x "$native"
for kind in alignment linkage abi naked control_globals weak_function section_function control_asm aligned_global scalar_global weak_scalar_global; do
  fixture="/workspace/bootstrap/fixtures/edg-gnu-attributes/${kind}.c"
  output="/audit-tmp/edg-gnu-${kind}.o"
  rm -f "$output"
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 90s \
      "$native" "$output" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$fixture"
  ) > "/audit-tmp/edg-gnu-${kind}-native.log" 2>&1
  kind_rc=$?
  set -e
  echo "EDG_GNU_ATTR_NATIVE_${kind^^}_EXIT=$kind_rc"
  if (( kind_rc != 0 )); then
    # This is a public source fixture, so a short structured rejection
    # message is safe. Do not apply this to the private real-C logs.
    python3 - "/audit-tmp/edg-gnu-${kind}-native.log" "${kind^^}" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text(errors="replace").splitlines()
matches = [line.split("rejected source:", 1)[-1].strip()
           for line in lines if "rejected source:" in line]
if matches:
    print("EDG_GNU_ATTR_NATIVE_" + sys.argv[2] + "_REJECTION=" + matches[0][:240])
else:
    print("EDG_GNU_ATTR_NATIVE_" + sys.argv[2] + "_REJECTION=no_native_adapter_diagnostic")
PY
  fi
  if (( kind_rc == 0 )); then
    # Check that success actually produced an ELF64 object without depending
    # on the absent 'file' utility in the pinned EDG container.
    python3 - "$output" <<'PY'
from pathlib import Path
import sys
data = Path(sys.argv[1]).read_bytes()
assert data[:5] == bytes([127, 69, 76, 70, 2]), "native output must be ELF64"
PY
    echo "EDG_GNU_ATTR_NATIVE_${kind^^}_OBJECT=ELF64"
  fi
done

if (( parse_rc != 0 )); then
  echo "EDG_C_TYPEID_EXIT=not_run_parser_failed"
  exit 0
fi

# Same native TypeId gate for the real C unit that passed frontend parsing.
set +e
(
  cd /workspace/edgcpp/build/gcc-release
  timeout --signal=TERM --kill-after=10s 90s \
    "$native" /audit-tmp/mrustc-edg-native.o --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$AUDIT_SOURCE"
) > /audit-tmp/edg-generated-c-native.log 2>&1
native_rc=$?
set -e
echo "EDG_C_TYPEID_EXIT=$native_rc"
# Native compiler diagnostics may contain private generated source lines:
# expose only exit status and a bounded error-category count.
python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re, sys
data = Path(sys.argv[1]).read_text(errors="replace")
print("EDG_C_TYPEID_DIAGNOSTIC_COUNTS "
      + " ".join(f"{word}={len(re.findall(re.escape(word), data, re.I))}"
                 for word in ("error", "unsupported", "reject", "invalid", "panic")))
PY
if (( native_rc == 0 )); then
  test -s /audit-tmp/mrustc-edg-native.o
  python3 - /audit-tmp/mrustc-edg-native.o <<'PY'
from pathlib import Path
import sys
data = Path(sys.argv[1]).read_bytes()
assert data[:5] == bytes([127, 69, 76, 70, 2]), "native output must be ELF64"
PY
  echo "EDG_C_TYPEID_OBJECT_CREATED=1 (semantics not yet compared with GCC)"
fi
