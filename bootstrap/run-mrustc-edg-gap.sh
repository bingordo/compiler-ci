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
timeout --signal=TERM --kill-after=10s 20m cargo build --quiet \
  --manifest-path edgcpp-rust/Cargo.toml --example parse
parser=/workspace/edgcpp-rust/target/debug/examples/parse
test -x "$parser"
set +e
(
  cd /workspace/edgcpp/build/gcc-release
  timeout --signal=TERM --kill-after=10s 90s \
    "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$AUDIT_SOURCE"
) > /audit-tmp/edg-generated-c-parse.log 2>&1
parse_rc=$?
set -e
echo "EDG_C_PARSE_EXIT=$parse_rc"
echo "EDG parse initial/trailing diagnostic output:"
grep -Ei 'error|unsupported|warning|fatal|undefined|unrecognized' /audit-tmp/edg-generated-c-parse.log | head -n 12 || true
tail -n 8 /audit-tmp/edg-generated-c-parse.log || true

if (( parse_rc != 0 )); then
  echo "EDG_C_TYPEID_EXIT=not_run_parser_failed"
  exit 0
fi

# A successful frontend parse alone does not prove our adapter supports the
# source. Attempt native TypeId-to-ELF independently and report first blocker.
timeout --signal=TERM --kill-after=10s 20m cargo build --quiet \
  --manifest-path compiler/tools/edg-driver/Cargo.toml
native=/workspace/compiler/tools/edg-driver/target/debug/compiler-edg-driver
test -x "$native"
set +e
(
  cd /workspace/edgcpp/build/gcc-release
  timeout --signal=TERM --kill-after=10s 90s \
    "$native" /audit-tmp/mrustc-edg-native.o --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$AUDIT_SOURCE"
) > /audit-tmp/edg-generated-c-native.log 2>&1
native_rc=$?
set -e
echo "EDG_C_TYPEID_EXIT=$native_rc"
echo "EDG native TypeId first error/diagnostics:"
grep -Ei 'error|unsupported|reject|failed|not implemented|invalid' \
  /audit-tmp/edg-generated-c-native.log | head -n 15 || true
tail -n 10 /audit-tmp/edg-generated-c-native.log || true
if (( native_rc == 0 )); then
  test -s /audit-tmp/mrustc-edg-native.o
  file /audit-tmp/mrustc-edg-native.o
  echo "EDG_C_TYPEID_OBJECT_CREATED=1 (semantics not yet compared with GCC)"
fi
