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

# The candidate bridge must preserve both language modes. Run the new
# integration tests against the already-prepared pinned upstream EDG archive.
if [[ "${EDG_C_RECORD_REGRESSION:-0}" == "1" ]]; then
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 12m cargo test \
      --manifest-path /workspace/edgcpp-rust/Cargo.toml \
      --test c_record_proof_mode -- --nocapture
  ) > /audit-tmp/edg-bridge-c-record-regression.log 2>&1
  test_rc=$?
  set -e
  echo "EDG_BRIDGE_C_RECORD_TEST_EXIT=$test_rc"
  if (( test_rc != 0 )); then
    # Emit ONLY bounded public-test error messages in a stable structured
    # summary that survives the surrounding workflow's tail/log filtering.
    python3 - /audit-tmp/edg-bridge-c-record-regression.log <<'PY'
from pathlib import Path
import re
import sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
interesting=[]
for line in lines:
    if any(p in line for p in (
        "panicked at", "FAILED", "test result:", "assertion",
        "C external", "C++ external", "C++ record", "C records",
        "C aggregate", "EDG must", "bridge must", "symbol",
        "error[E", "error:")):
        msg=re.sub(r"/workspace/[^ :]+", "<fixture>", line.strip())
        if len(msg)>280: msg=msg[:280]
        interesting.append(msg)
for i,msg in enumerate(interesting[-14:],1):
    print(f"EDG_BRIDGE_C_RECORD_TEST_DIAG_{i}={msg}")
PY
  fi
fi

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

# Record bounded, source-free process evidence separately from the EDG
# parser's own diagnostics. In particular, distinguish timeout's 124/137
# status from a genuine signal and memory pressure from fast parser failure.
# GNU time is optional in the pinned container: absence is reported as an
# unknown memory measurement, not mistaken for zero RSS.
probe_generated_c() {
  local prefix="$1" input="$2" limit="$3" logfile="$4" stack_kb="${5:-}"
  local started ended rc rss="unavailable" timed_log="${logfile}.resources"
  echo "${prefix}_BYTES=$(stat -c %s "$input")"
  started=$(date +%s)
  set +e
  if [[ -x /usr/bin/time ]]; then
    (
      cd /workspace/edgcpp/build/gcc-release
      if [[ -n "$stack_kb" ]]; then ulimit -s "$stack_kb"; fi
      timeout --signal=TERM --kill-after=10s "$limit" \
        /usr/bin/time -f 'PEAK_RSS_KB=%M' -o "$timed_log" \
          "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$input"
    ) > "$logfile" 2>&1
    rc=$?
  else
    (
      cd /workspace/edgcpp/build/gcc-release
      if [[ -n "$stack_kb" ]]; then ulimit -s "$stack_kb"; fi
      timeout --signal=TERM --kill-after=10s "$limit" \
        "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$input"
    ) > "$logfile" 2>&1
    rc=$?
  fi
  set -e
  ended=$(date +%s)
  if [[ -s "$timed_log" ]]; then
    rss=$(awk -F= '/^PEAK_RSS_KB=[0-9]+$/ {print $2; exit}' "$timed_log")
    [[ -n "$rss" ]] || rss="unavailable"
  fi
  last_generated_c_probe_rc=$rc
  echo "${prefix}_EXIT=$rc"
  echo "${prefix}_WALL_SECONDS=$((ended-started))"
  echo "${prefix}_PEAK_RSS_KB=$rss"
  grep -m 1 '^EDG_PROBE_RESULT ' "$logfile" || true
  case "$rc" in
    0) echo "${prefix}_CLASS=accepted" ;;
    124|137) echo "${prefix}_CLASS=timeout_or_forced_kill" ;;
    139) echo "${prefix}_CLASS=segmentation_fault" ;;
    134) echo "${prefix}_CLASS=abort_signal" ;;
    1|2) echo "${prefix}_CLASS=frontend_or_bridge_rejection" ;;
    *) if (( rc >= 128 )); then
         echo "${prefix}_CLASS=other_signal"
       else
         echo "${prefix}_CLASS=other_nonzero"
       fi ;;
  esac
}

# Probe a SMALL real generated TU first; the previously failing 53.6 MiB
# compiler-ir output is separately measured, and never treated as accepted
# just because the smaller source parses. No private source excerpts logged.
echo "EDG_C_SAMPLE_BYTES=$(stat -c %s "$AUDIT_SOURCE")"
probe_generated_c EDG_C_PARSE "$AUDIT_SOURCE" 90s /audit-tmp/edg-generated-c-parse.log
# The subsequent large-file probe updates the helper's last exit code.
# Preserve the SMALL source's result for its own native TypeId gate.
parse_rc=$last_generated_c_probe_rc

# Independent upstream frontend control: build EDG's normal `cpfe`
# executable from the same pinned upstream sources as the callable library.
# Unlike the Rust/C++ callback bridge, this runs EDG's own source frontend
# with --no_code_gen, so a successful parse distinguishes a bridge crash
# from a native EDG parsing failure.
# Opt-in here: routine bootstrap audits must not build a second frontend.
if [[ "${EDG_UPSTREAM_CHECK:-0}" == "1" ]]; then
  echo "EDG_UPSTREAM_REVISION=$EDGCPP_EDG_REVISION"
  echo "EDG_UPSTREAM_INPUT_BYTES=$(stat -c %s "$AUDIT_SOURCE")"
  echo "EDG_UPSTREAM_INPUT_SHA256=$(sha256sum "$AUDIT_SOURCE" | cut -d' ' -f1)"
  upstream=/workspace/edgcpp/build/gcc-release/bin/cpfe
  set +e
  timeout --signal=TERM --kill-after=10s 14m \
    cmake --build /workspace/edgcpp/build/gcc-release --target cpfe --parallel 2 \
    > /audit-tmp/edg-upstream-build.log 2>&1
  build_rc=$?
  set -e
  echo "EDG_UPSTREAM_BUILD_EXIT=$build_rc"
  if (( build_rc == 0 )) && [[ -x "$upstream" ]]; then
    # A public fixture proves this separate executable was configured
    # correctly before attributing any real input failure to upstream EDG.
    for kind in control real; do
      if [[ "$kind" == "control" ]]; then
        input=/workspace/bootstrap/fixtures/edg-gnu-attributes/alignment.c
      else
        input="$AUDIT_SOURCE"
      fi
      set +e
      (
        cd /workspace/edgcpp/build/gcc-release
        ulimit -c 0
        timeout --signal=TERM --kill-after=10s 120s \
          "$upstream" --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" --no_code_gen \
          "${include_args[@]}" "$input"
      ) > "/audit-tmp/edg-upstream-$kind.log" 2>&1
      upstream_rc=$?
      set -e
      if [[ "$kind" == "real" ]]; then
        echo "EDG_UPSTREAM_REAL_C_EXIT=$upstream_rc"
      else
        echo "EDG_UPSTREAM_CONTROL_EXIT=$upstream_rc"
      fi
    done
    echo "EDG_UPSTREAM_COMPARISON_BRIDGE_EXIT=$parse_rc"
  else
    echo "EDG_UPSTREAM_RESULT=not_tested_frontend_build_failed"
  fi
fi

# Debug native SIGSEGV on the smallest real mrustc C input; a Rust panic
# backtrace cannot diagnose EDG's C++/FFI segfault.
# This expensive diagnostic is enabled only by this PR/manual opt-in;
# regular main bootstrap retains its cheap parse/stack result without APT.
# Keep the raw GDB log and generated C runner-private. Publish only bounded
# frames with arguments and paths removed.
if (( parse_rc == 139 )) && [[ "${EDG_RUN_CREDUCE:-0}" == "1" ]]; then
  echo "EDG_C_GDB_TRIGGER=small_real_source_sigsegv"
  # Install binary distro packages only ONCE in this container, not per
  # C-Reduce candidate. The mounted /audit-tmp cache contains .deb archives
  # restored across ephemeral GitHub runners by actions/cache; APT still
  # verifies package metadata and dependencies before installation.
  debug_packages=(gdb)
  if [[ "${EDG_RUN_CREDUCE:-0}" == "1" ]]; then
    debug_packages+=(creduce)
  fi
  need_packages=false
  for package in "${debug_packages[@]}"; do
    if ! command -v "$package" >/dev/null 2>&1; then
      need_packages=true
    fi
  done
  if [[ "$need_packages" == "true" ]] && command -v apt-get >/dev/null 2>&1; then
    apt_cache=/audit-tmp/edg-debug-apt-cache
    mkdir -p "$apt_cache/partial"
    chmod 1777 "$apt_cache" "$apt_cache/partial"
    set +e
    DEBIAN_FRONTEND=noninteractive apt-get update -qq \
      > /audit-tmp/edg-debug-apt.log 2>&1 &&
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      -o "Dir::Cache::archives=$apt_cache" \
      -o 'APT::Keep-Downloaded-Packages=true' \
      --no-install-recommends "${debug_packages[@]}" \
      >> /audit-tmp/edg-debug-apt.log 2>&1
    apt_rc=$?
    set -e
    echo "EDG_CREDUCE_APT_INSTALL_EXIT=$apt_rc"
  fi
  if [[ "${EDG_RUN_CREDUCE:-0}" == "1" ]] &&
     command -v creduce >/dev/null 2>&1; then
    # Log only the binary package version. All install logs stay runner-local.
    echo "EDG_CREDUCE_PACKAGE_VERSION=$(dpkg-query -W -f='${Version}' creduce 2>/dev/null || echo unknown)"
  fi
  if command -v gdb >/dev/null 2>&1; then
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      timeout --signal=TERM --kill-after=10s 120s \
        gdb -nx --batch --quiet \
          -ex 'set pagination off' \
          -ex 'set confirm off' \
          -ex 'set debuginfod enabled off' \
          -ex 'set print frame-arguments none' \
          -ex 'set print static off' \
          -ex 'run' \
          -ex 'thread apply all bt 32' \
          --args "$parser" --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "${include_args[@]}" "$AUDIT_SOURCE"
    ) > /audit-tmp/edg-small-real-gdb.log 2>&1
    gdb_rc=$?
    set -e
    python3 - /audit-tmp/edg-small-real-gdb.log "$gdb_rc" <<'PY'
from pathlib import Path
import re
import sys
raw = Path(sys.argv[1]).read_text(errors="replace")
print("EDG_C_GDB_EXIT=" + sys.argv[2])
print("EDG_C_GDB_SIGSEGV=" + ("yes" if "Program received signal SIGSEGV" in raw else "no"))
frames = []
for line in raw.splitlines():
    if not re.match(r"^\s*#\d+\s+", line):
        continue
    line = re.sub(r"\s+(?:at|from)\s+.*$", "", line.strip())
    line = re.sub(r"\([^()]*\)", "(...)", line)
    line = re.sub(r"/(?:[^\s]+)", "<path>", line)
    frames.append(line[:240])
print("EDG_C_GDB_FRAMES=" + str(len(frames)))
for n, frame in enumerate(frames[:32]):
    print(f"EDG_C_GDB_FRAME_{n}={frame}")
PY
  else
    echo "EDG_C_GDB_STATUS=unavailable_no_gdb"
  fi
else
  echo "EDG_C_GDB_TRIGGER=not_requested_or_no_sigsegv"
fi

# Only minimize the real C input if the initial EDG run truly SIGSEGVed.
# C-Reduce uses GDB as its interestingness test to preserve the exact
# plain_cpp_record top frame, not merely any crash or frontend failure.
# The original and the minimized source stay inside the mounted runner temp.
if (( parse_rc == 139 )) && [[ "${EDG_RUN_CREDUCE:-0}" == "1" ]]; then
  set +e
  bash /workspace/bootstrap/reduce-mrustc-edg-crash.sh \
    "$parser" "$gnu_number" "$AUDIT_SOURCE" "${include_args[@]}" \
    > /audit-tmp/edg-creduce-summary.log 2>&1
  reduce_wrapper_rc=$?
  set -e
  echo "EDG_CREDUCE_WRAPPER_EXIT=$reduce_wrapper_rc"
  grep -E '^EDG_CREDUCE_[A-Z0-9_]+=' /audit-tmp/edg-creduce-summary.log || true
else
  echo "EDG_CREDUCE_STATUS=not_requested_or_no_small_source_sigsegv"
fi
if [[ -n "${AUDIT_LARGE_SOURCE:-}" && -s "$AUDIT_LARGE_SOURCE" &&
      "$AUDIT_LARGE_SOURCE" != "$AUDIT_SOURCE" ]]; then
  echo "EDG_C_LARGE_BYTES=$(stat -c %s "$AUDIT_LARGE_SOURCE")"
  probe_generated_c EDG_C_LARGE_PARSE "$AUDIT_LARGE_SOURCE" 90s /audit-tmp/edg-large-c-parse.log
  # Only after a genuine SIGSEGV: compare with a 64 MiB parser stack.
  # Stack sensitivity is evidence, not proof of stack overflow.
  # Avoid retrying successes, language rejections or timeouts.
  if (( last_generated_c_probe_rc == 139 )); then
    echo "EDG_C_LARGE_STACK64M_RETRY=triggered_after_sigsegv"
    probe_generated_c EDG_C_LARGE_STACK64M_PARSE "$AUDIT_LARGE_SOURCE" 90s \
      /audit-tmp/edg-large-c-stack64m-parse.log 65536
  else
    echo "EDG_C_LARGE_STACK64M_RETRY=not_applicable"
  fi
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
  # Only public source fixtures get EDG symbol/global details in CI logs.
  provenance_args=()
  if [[ "$kind" == scalar_global || "$kind" == weak_scalar_global ]]; then
    provenance_args=(--dump-edg-global-provenance)
  fi
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 90s \
      "$native" "$output" ${provenance_args[@]} --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$fixture"
  ) > "/audit-tmp/edg-gnu-${kind}-native.log" 2>&1
  kind_rc=$?
  set -e
  echo "EDG_GNU_ATTR_NATIVE_${kind^^}_EXIT=$kind_rc"
  if [[ "$kind" == scalar_global || "$kind" == weak_scalar_global ]]; then
    # Report exact EDG source facts without guessing that absence of an
    # attribute record implies the attribute was not present in source.
    # Keep output tightly bounded even for these public fixtures.
    grep -E '^(EDG_GLOBAL_PROVENANCE|GLOBAL id=|  VARIABLE_INIT|  INTEGER_INIT)' \
      "/audit-tmp/edg-gnu-${kind}-native.log" | head -n 12 |
      sed "s/^/EDG_C_SCALAR_PROVENANCE_${kind^^} /" || true
  fi
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
