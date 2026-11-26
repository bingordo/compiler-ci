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
# Use the exact EDG bridge that the candidate compiler actually pins,
# so the source/parser census and native TypeId gate cover one coherent ABI.
# A stale standalone EDG bridge would misclassify already-fixed native gaps.
EDG_BRIDGE_SHA="$(python3 - <<'PY'
import re, tomllib
with open('/workspace/compiler/tools/edg-driver/Cargo.toml','rb') as file:
    dep = tomllib.load(file)['dependencies']['edgcpp']
assert dep['git'] == 'https://github.com/bingordo/edgcpp-rust.git'
sha = dep['rev']
assert re.fullmatch(r'[0-9a-f]{40}', sha)
print(sha)
PY
)"
# Opt-in diagnostic pin override for testing a bridge fix against the exact
# same generated-C compiler revision. Normal production audit remains pinned
# to the compiler driver dependency above.
if [[ -n "${EDG_BRIDGE_OVERRIDE_SHA:-}" ]]; then
  [[ "$EDG_BRIDGE_OVERRIDE_SHA" =~ ^[0-9a-f]{40}$ ]]
  echo "EDG_C_SOURCE_BRIDGE_COMPILER_PIN=$EDG_BRIDGE_SHA"
  EDG_BRIDGE_SHA="$EDG_BRIDGE_OVERRIDE_SHA"
  echo "EDG_C_SOURCE_BRIDGE_TEST_OVERRIDE=yes"
fi
test "$(git -C edgcpp-rust rev-parse HEAD)" = "$EDG_BRIDGE_SHA"
echo "EDG_C_SOURCE_BRIDGE_SHA=$EDG_BRIDGE_SHA"
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


version="$(gcc -dumpfullversion -dumpversion)"
IFS=. read -r major minor patch <<< "$version"
gnu_number=$((10#${major}*10000+10#${minor:-0}*100+10#${patch:-0}))
mapfile -t gcc_includes < <(gcc -E -x c /dev/null -v 2>&1 | awk '/#include <...> search starts here:/{active=1;next} /^End of search list/{active=0} active && /^ /{sub(/^[[:space:]]+/, "");print}')
include_args=()
for dir in "${gcc_includes[@]}"; do
  if [[ -d "$dir" ]]; then include_args+=("-I$dir"); fi
done
cargo build --release --manifest-path /workspace/edgcpp-rust/Cargo.toml --features codegen --example codegen
export EDG_CLIF=/workspace/edgcpp-rust/target/release/examples/codegen
cd /workspace/edgcpp/build/gcc-release
python3 /workspace/bootstrap/probe-mrustc-cranelift-reference.py edg "$EDG_CLIF" /workspace/mrustc/output-compiler-bootstrap --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}"
