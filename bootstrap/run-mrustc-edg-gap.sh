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
if [[ "${EDG_LITERAL_REGRESSION:-0}" == "1" ]]; then
  cp bootstrap/edg_literal_parse_probe.rs edgcpp-rust/examples/ci_edg_parse_probe.rs
else
  cp bootstrap/edg_parse_probe.rs edgcpp-rust/examples/ci_edg_parse_probe.rs
fi
timeout --signal=TERM --kill-after=10s 20m cargo build --quiet \
  --manifest-path edgcpp-rust/Cargo.toml --example ci_edg_parse_probe
parser=/workspace/edgcpp-rust/target/debug/examples/ci_edg_parse_probe
test -x "$parser"
ulimit -c 0

# A public C11 source is a separate, independently executable GCC oracle
# for the immutable string-object relocation witness. This runs inside the
# pinned EDG environment and does not publish generated compiler C or literal
# contents from that private translation unit.
if [[ "${EDG_LITERAL_REGRESSION:-0}" == "1" ]]; then
  public=/workspace/edgcpp-rust/tests/fixtures/bootstrap_literal_union.c
  test -s "$public"
  set +e
  timeout --signal=TERM --kill-after=3s 30s gcc -std=c11 -O2 -Wall -Wextra -Werror     "$public" -o /audit-tmp/bootstrap-literal-gcc &&
    timeout --signal=TERM --kill-after=3s 10s /audit-tmp/bootstrap-literal-gcc
  gcc_rc=$?
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 8m cargo run --quiet       --manifest-path /workspace/edgcpp-rust/Cargo.toml       --example audit_static_literal_images -- --gcc --c11       --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$public"
  ) > /audit-tmp/edg-static-literal-public.log 2>&1
  bridge_rc=$?
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 5m cargo test --quiet       --manifest-path /workspace/edgcpp-rust/Cargo.toml --lib       bootstrap_literal_global_wire_tests
  ) > /audit-tmp/edg-static-literal-wire.log 2>&1
  wire_rc=$?
  set -e
  echo "EDG_C_LITERAL_GCC_EXIT=$gcc_rc"
  echo "EDG_C_LITERAL_BRIDGE_EXIT=$bridge_rc"
  echo "EDG_C_LITERAL_WIRE_EXIT=$wire_rc"
  if (( wire_rc != 0 )); then
    # Public bridge test only. Sanitized, bounded diagnostic lines are
    # explicitly whitelisted by the parent job's numeric/source-free log
    # selection; no private mrustc file is involved.
    python3 - /audit-tmp/edg-static-literal-wire.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if any(token in line for token in
    ("error[E","error:","panicked at","FAILED","test result:","expected","found"))]
for n,line in enumerate(selected[-12:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_LITERAL_TEST_DIAG_{n}={msg[:250]}")
PY
    echo 'EDG_LITERAL_PUBLIC_WIRE_TEST_FAILURE_DETAIL_BEGIN'
    # Only source-backed public in-repo unit tests appear in this file; do
    # not print any private generated-C compiler/error output.
    tail -n 32 /audit-tmp/edg-static-literal-wire.log |
      sed -E 's@/workspace/[^ :]+@<public-path>@g' || true
    echo 'EDG_LITERAL_PUBLIC_WIRE_TEST_FAILURE_DETAIL_END'
  fi
  if (( bridge_rc != 0 || wire_rc != 0 )); then
    # This fixture and test are committed public code; limit diagnostics to
    # compiler errors and summary, never leak the private mrustc source.
    grep -E '(^error|error\\[E|EDG_STATIC_LITERAL|FAILED|test result:)'       /audit-tmp/edg-static-literal-public.log       /audit-tmp/edg-static-literal-wire.log | tail -n 14 || true
  fi
fi

# C11 original function-address reference is an independent source family.
# It must retain the EDG routine SymbolId and cannot borrow literal IDs.
if [[ "${EDG_LITERAL_REGRESSION:-0}" == "1" ]]; then
  routine_public=/workspace/edgcpp-rust/tests/fixtures/bootstrap_static_routine_union.c
  test -s "$routine_public"
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 8m cargo run --quiet \
      --manifest-path /workspace/edgcpp-rust/Cargo.toml \
      --example audit_static_routine_union -- --gcc --c11 \
      --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$routine_public"
  ) > /audit-tmp/edg-static-routine-public.log 2>&1
  routine_bridge_rc=$?
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 5m cargo test --quiet \
      --manifest-path /workspace/edgcpp-rust/Cargo.toml --lib \
      bootstrap_static_routine_relocation_wire_tests
  ) > /audit-tmp/edg-static-routine-wire.log 2>&1
  routine_wire_rc=$?
  set -e
  echo "EDG_C_ROUTINE_BRIDGE_EXIT=$routine_bridge_rc"
  echo "EDG_C_ROUTINE_WIRE_EXIT=$routine_wire_rc"
  if (( routine_bridge_rc != 0 || routine_wire_rc != 0 )); then
    python3 - /audit-tmp/edg-static-routine-public.log \
      /audit-tmp/edg-static-routine-wire.log <<'PY'
from pathlib import Path
import re,sys
for p in sys.argv[1:]:
    lines=Path(p).read_text(errors="replace").splitlines()
    selected=[line for line in lines if any(k in line for k in
        ("error[E","error:","panicked","FAILED","test result:"))]
    for i,line in enumerate(selected[-8:],1):
        msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
        msg=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","?",msg)
        print(f"EDG_C_ROUTINE_PUBLIC_DIAG_{i}={msg[:230]}")
PY
  fi
fi

# Original GNU C11 strong-data address plus weak union routine-address
# differential. The source fixture and EDG auditor are committed public
# code; never expose the private generated mrustc translation unit.
if [[ "${EDG_WEAK_DATA_WORD_ORACLE:-0}" == "1" ]]; then
  weak_source=/workspace/edgcpp-rust/tests/fixtures/bootstrap_weak_word_variable_addresses.c
  test -s "$weak_source"
  set +e
  timeout --signal=TERM --kill-after=3s 30s gcc -std=gnu11 \
    -O2 -Wall -Wextra -Werror -c "$weak_source" -o /audit-tmp/weak-data-word-gcc.o
  weak_data_gcc_rc=$?
  if (( weak_data_gcc_rc == 0 )); then
    rout_reloc_count=$(readelf -rW /audit-tmp/weak-data-word-gcc.o |
      grep -cE 'R_X86_64_64.*bootstrap_weak_word_target' || true)
    data_reloc_count=$(readelf -rW /audit-tmp/weak-data-word-gcc.o |
      grep -cE 'R_X86_64_64.*bootstrap_weak_data_target' || true)
    weak_def_count=$(readelf -Ws /audit-tmp/weak-data-word-gcc.o |
      awk '$4=="OBJECT" && $5=="WEAK" &&
           $8 ~ /^bootstrap_weak_word[568]$/ {n++} END {print n+0}')
    if [[ "$rout_reloc_count" != 3 || "$data_reloc_count" != 3 ||
          "$weak_def_count" != 3 ]]; then
      weak_data_gcc_rc=1
    fi
  fi
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 8m cargo run --quiet \
      --manifest-path /workspace/edgcpp-rust/Cargo.toml \
      --example audit_weak_word_variable_addresses -- --gcc --c11 \
      --no_strict_gnu --gnu_version "$gnu_number" "${include_args[@]}" "$weak_source"
  ) > /audit-tmp/edg-public-weak-data-word.log 2>&1
  weak_data_edg_rc=$?
  set -e
  echo "EDG_C_WEAK_DATA_WORD_GCC_EXIT=$weak_data_gcc_rc"
  echo "EDG_C_WEAK_DATA_WORD_EDG_EXIT=$weak_data_edg_rc"
  if (( weak_data_edg_rc != 0 )); then
    # The only source of diagnostics is the committed public C fixture.
    grep -E '(error\[E|^error|panicked|source-owned|weak union|relocation)' \
      /audit-tmp/edg-public-weak-data-word.log | tail -n 12 |
      sed -E 's@/workspace/[^ :]+@<public-fixture>@g' || true
  fi
fi

# Source-pinned TypeId consumer compile check, deliberately separate from
# production compiler checkout. The candidate shares the same checked EDG
# dependency as the public producer. No source from the private mrustc TU is
# supplied to this check.
if [[ "${EDG_LITERAL_REGRESSION:-0}" == "1" ]]; then
  # The workflow pins the checked-out candidate to an immutable full SHA.
  # Report that exact checkout instead of duplicating a second, stale SHA
  # here: otherwise the public native ELF oracle never reaches the compiler.
  candidate_sha="$(git -c safe.directory=/workspace/compiler-literal-source -C /workspace/compiler-literal-source rev-parse HEAD)"
  if [[ ! "$candidate_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "EDG_C_LITERAL_TYPEID_CANDIDATE_PIN_INVALID"
    exit 1
  fi
  # Actions checkout is mounted read-only. Make a disposable compilation
  # copy in the private runner temp so Cargo can update its local lockfile.
  mkdir -p /audit-tmp/compiler-literal-candidate
  tar -C /workspace/compiler-literal-source --exclude=.git -cf - . |
    tar -C /audit-tmp/compiler-literal-candidate -xf -
  # The exact immutable bridge is already checked out and built locally.
  # Resolve it through Cargo's source patch rather than trying to git-fetch
  # from the isolated CI Docker container.
  edg_patch='patch."https://github.com/bingordo/edgcpp-rust.git".edgcpp.path="/workspace/edgcpp-rust"'
  echo "EDG_C_LITERAL_TYPEID_CANDIDATE_SHA=$candidate_sha"
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
      timeout --signal=TERM --kill-after=10s 12m cargo --config "$edg_patch" check --quiet \
      --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml
  ) > /audit-tmp/edg-typeid-literal-candidate-check.log 2>&1
  typeid_check_rc=$?
  set -e
  echo "EDG_C_LITERAL_TYPEID_CHECK_EXIT=$typeid_check_rc"
  if (( typeid_check_rc != 0 )); then
    python3 - /audit-tmp/edg-typeid-literal-candidate-check.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if
          "error[E" in line or "error:" in line or "warning:" in line]
for i,line in enumerate(selected[-12:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_LITERAL_TYPEID_DIAG_{i}={msg[:230]}")
PY
  fi
  # Test direct TypeId admission against the *original* EDG C11 fixture.
  # The test mutates actual source event records to prove fail-closed
  # behavior for displaced/dangling pointers, orphan data and weak/TLS.
  typeid_unit_rc=11
  if (( typeid_check_rc == 0 )); then
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver public_c_literal_ -- --test-threads=1
    ) > /audit-tmp/edg-typeid-literal-unit.log 2>&1
    typeid_unit_rc=$?
    set -e
    if (( typeid_unit_rc != 0 )); then
      python3 - /audit-tmp/edg-typeid-literal-unit.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if any(
    needle in line for needle in ("error[E","error:","panicked","FAILED","test result:"))]
for i,line in enumerate(selected[-12:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_LITERAL_TYPEID_UNIT_DIAG_{i}={msg[:230]}")
PY
    fi
  fi
  echo "EDG_C_LITERAL_TYPEID_UNIT_EXIT=$typeid_unit_rc"
  # Source-independent reason-code tests live in the candidate compiler;
  # no generated C source is ever supplied to the numeric diagnostic.
  if [[ "${EDG_C_MIXED_GLOBAL_PROBE:-0}" == "1" ]]; then
    mixed_reason_rc=11
    if (( typeid_check_rc == 0 )); then
      set +e
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          --bin compiler-edg-driver mixed_global_ -- --test-threads=1
      ) > /audit-tmp/edg-mixed-reason-unit.log 2>&1
      mixed_reason_rc=$?
      set -e
    fi
    echo "EDG_C_MIXED_REASON_UNIT_EXIT=$mixed_reason_rc"
    if (( mixed_reason_rc != 0 )) && [[ -s /audit-tmp/edg-mixed-reason-unit.log ]]; then
      # The mixed_global_ suite is public, checked-in compiler test code.
      # Sanitizing still prevents paths, unexpected control characters or
      # accidental nonpublic EDG diagnostics escaping from the runner.
      python3 - /audit-tmp/edg-mixed-reason-unit.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if any(s in line for s in
    ("error[E","error:","panicked","FAILED","test result:"))]
for i,line in enumerate(selected[-8:],1):
    msg=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",msg)
    if msg:print(f"EDG_C_MIXED_REASON_DIAG_{i}={msg[:180]}")
PY
    fi
  fi

  # Original public C11 TypeId record and field witnesses certify only
  # the representable SysV INTEGER/INTEGER two-eightbyte aggregate shape.
  # No by-value lowering, invented integer carrier, or private mrustc
  # source is admitted on the strength of this separate layout test.
  if [[ "${EDG_C_MIXED_GLOBAL_PROBE:-0}" == "1" ]]; then
    gp_pair_rc=11
    if (( typeid_check_rc == 0 )); then
      set +e
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          --bin compiler-edg-driver c_gp_pair_formal_ -- --test-threads=1
      ) > /audit-tmp/edg-c-gp-pair-unit.log 2>&1
      gp_pair_rc=$?
      set -e
    fi
    echo "EDG_C_GP_PAIR_UNIT_EXIT=$gp_pair_rc"
    if (( gp_pair_rc != 0 )) && [[ -s /audit-tmp/edg-c-gp-pair-unit.log ]]; then
      python3 - /audit-tmp/edg-c-gp-pair-unit.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
errors=[line for line in lines if any(k in line for k in
        ("error[E","error:","panicked","FAILED","test result:"))]
for i,line in enumerate(errors[-8:],1):
    msg=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",msg)
    if msg:print(f"EDG_C_GP_PAIR_DIAG_{i}={msg[:180]}")
PY
    fi
  fi

  # Original public C11 typedef-local proof: exactly one source-owned
  # canonical layout, preserving scalar/pointer/array/record identity.
  # Keep this independent from the GP-pair ABI check and from private C.
  if [[ "${EDG_C_MIXED_GLOBAL_PROBE:-0}" == "1" ]]; then
    local_typedef_rc=11
    if (( typeid_check_rc == 0 )); then
      set +e
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          --bin compiler-edg-driver c_local_typedef_tests -- --test-threads=1
      ) > /audit-tmp/edg-c-local-typedef-unit.log 2>&1
      local_typedef_rc=$?
      set -e
    fi
    echo "EDG_C_LOCAL_TYPEDEF_UNIT_EXIT=$local_typedef_rc"
    if (( local_typedef_rc != 0 )) && [[ -s /audit-tmp/edg-c-local-typedef-unit.log ]]; then
      python3 - /audit-tmp/edg-c-local-typedef-unit.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
errors=[line for line in lines if any(k in line for k in
        ("error[E","error:","panicked","FAILED","test result:",
         "EDG_PUBLIC_C_BYTE_RETURN_REASON="))]
for i,line in enumerate(errors[-8:],1):
    msg=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",msg)
    if msg:print(f"EDG_C_LOCAL_TYPEDEF_DIAG_{i}={msg[:180]}")
PY
    fi
  fi

  # Original C record-pointer scalar fields. A source TypeId owns the
  # pointer ingress and the EDG field byte offsets. Independently compile
  # the same public C object with GCC, execute a separate GCC harness and
  # compare exact output; never consume private generated mrustc source.
  if [[ "${EDG_C_MIXED_GLOBAL_PROBE:-0}" == "1" ]]; then
    rec_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_c_local_typedefs.c
    rec_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_c_local_typedefs_harness.c
    test -s "$rec_source" && test -s "$rec_harness"
    rec_unit_rc=${local_typedef_rc:-11}
    rec_native_rc=11
    rec_gcc_rc=11
    rec_link_rc=11
    rec_run_rc=11
    rec_parity_rc=11
    set +e
    if (( rec_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/edg-c-record-ptr-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$rec_source"
      ) > /audit-tmp/edg-c-record-ptr-native.log 2>&1
      rec_native_rc=$?
    fi
    gcc -std=gnu11 -O0 -c "$rec_source" -o /audit-tmp/edg-c-record-ptr-gcc.o
    rec_gcc_rc=$?
    if (( rec_native_rc == 0 && rec_gcc_rc == 0 )); then
      for obj in /audit-tmp/edg-c-record-ptr-typeid.o /audit-tmp/edg-c-record-ptr-gcc.o; do
        count=$(readelf -Ws "$obj" | awk '$4=="FUNC" && $5=="GLOBAL" &&
          $8 ~ /^bootstrap_c_(local_aliases|record_pointer_(formal|get|set|truth|negated|equal|forward|forward_set|address_local|address_truth)|scalar_pointer_(truth|unequal)|pair_(inreg|forward|local|external_forward|copy_ptr|deref_field|deref_forward)|implicit_(scalar_assignment|record_field)|or_(local_field|gp_field|signed32_unsigned64|unsigned32_signed64|signed32_unsigned32|nested_shift)|shift_(left64_count32|right64_count32|left32_count64)|field_(compound_lshift|compound_rshift|compound_mixed|compound_add|post_increment)|local_compound_update|while_(predec|postdec|record_predec|record_postdec|local_field_predec)|if_postdec|discarded_cast_(local|record|call|comma))$/ {n++}
          END {print n+0}')
        if [[ "$count" != 47 ]]; then
          if [[ "$obj" == *typeid.o ]]; then rec_native_rc=12
          else rec_gcc_rc=12; fi
        fi
      done
    fi
    if (( rec_native_rc == 0 && rec_gcc_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-record-ptr-gcc.o "$rec_harness" \
        -o /audit-tmp/edg-c-record-ptr-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-record-ptr-typeid.o "$rec_harness" \
        -o /audit-tmp/edg-c-record-ptr-typeid-exe
      rec_link_rc=$?
      if (( rec_link_rc == 0 )); then
        /audit-tmp/edg-c-record-ptr-gcc-exe > /audit-tmp/edg-c-record-ptr-gcc.out &&
        /audit-tmp/edg-c-record-ptr-typeid-exe > /audit-tmp/edg-c-record-ptr-typeid.out
        rec_run_rc=$?
        if (( rec_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/edg-c-record-ptr-gcc.out \
            /audit-tmp/edg-c-record-ptr-typeid.out
          rec_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_RECORD_PTR_UNIT_EXIT=$rec_unit_rc"
    echo "EDG_C_RECORD_PTR_ELF_EXIT=$rec_native_rc"
    echo "EDG_C_RECORD_PTR_GCC_EXIT=$rec_gcc_rc"
    echo "EDG_C_RECORD_PTR_LINK_EXIT=$rec_link_rc"
    echo "EDG_C_RECORD_PTR_RUN_EXIT=$rec_run_rc"
    echo "EDG_C_RECORD_PTR_PARITY_EXIT=$rec_parity_rc"
    if (( rec_native_rc != 0 || rec_unit_rc != 0 )); then
      python3 - /audit-tmp/edg-c-record-ptr-native.log \
        /audit-tmp/edg-c-local-typedef-unit.log <<'PY'
from pathlib import Path
import re,sys
hits=[]
for path in map(Path,sys.argv[1:]):
    if not path.is_file():continue
    for line in path.read_text(errors="replace").splitlines():
        if any(v in line for v in ("error[E","error:","FAILED","panicked",
                                   "test result:")):
            line=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",line.strip())
            line=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",line)
            hits.append(line[:180])
for i,msg in enumerate(hits[-8:],1):
    print(f"EDG_C_RECORD_PTR_DIAG_{i}={msg}")
PY
    fi
  fi

  # Full public GNU C11 weak data/routine combined proof. The source
  # fixture and harness are both committed with the candidate compiler.
  # Private mrustc generated C is not part of this positive oracle.
  if [[ "${EDG_WEAK_TYPEID_ORACLE:-0}" == "1" ]]; then
    weak_src=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_weak_union_word_data.c
    weak_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_weak_union_word_data_harness.c
    test -s "$weak_src" && test -s "$weak_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver weak_c_word_array_relocations -- --test-threads=1
    ) > /audit-tmp/weak-word-typeid-unit.log 2>&1
    weak_unit_rc=$?
    set -e
    echo "EDG_C_WEAK_TYPEID_UNIT_EXIT=$weak_unit_rc"
    if (( weak_unit_rc != 0 )); then
      python3 - /audit-tmp/weak-word-typeid-unit.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
errors=[x for x in lines if any(k in x for k in
    ("error[E","error:","panicked","FAILED","test result:"))]
for i,msg in enumerate(errors[-14:],1):
    msg=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","<public-fixture>",msg.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_WEAK_TYPEID_UNIT_DIAG_{i}={msg[:240]}")
PY
    fi
    weak_native_rc=11
    if (( weak_unit_rc == 0 )); then
      set +e
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/weak-word-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$weak_src"
      ) > /audit-tmp/weak-word-typeid-object.log 2>&1
      weak_native_rc=$?
      set -e
    fi
    set +e
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$weak_src" \
      -o /audit-tmp/weak-word-gcc.o
    weak_gcc_rc=$?
    set -e
    if (( weak_gcc_rc == 0 && weak_native_rc == 0 )); then
      for obj in /audit-tmp/weak-word-gcc.o /audit-tmp/weak-word-typeid.o; do
        ro=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*bootstrap_weak_word_target' || true)
        da=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*bootstrap_weak_data_target' || true)
        wk=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="WEAK" &&
               $8 ~ /^bootstrap_weak_word[568]$/ {n++} END {print n+0}')
        if [[ "$ro" != 3 || "$da" != 3 || "$wk" != 3 ]]; then
          if [[ "$obj" == *typeid.o ]]; then weak_native_rc=12
          else weak_gcc_rc=12; fi
        fi
      done
    fi
    weak_link_rc=11
    weak_run_rc=11
    weak_parity_rc=11
    if (( weak_gcc_rc == 0 && weak_native_rc == 0 )); then
      set +e
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/weak-word-gcc.o "$weak_harness" -o /audit-tmp/weak-word-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/weak-word-typeid.o "$weak_harness" -o /audit-tmp/weak-word-typeid-exe
      weak_link_rc=$?
      if (( weak_link_rc == 0 )); then
        /audit-tmp/weak-word-gcc-exe > /audit-tmp/weak-word-gcc.out &&
        /audit-tmp/weak-word-typeid-exe > /audit-tmp/weak-word-typeid.out
        weak_run_rc=$?
        if (( weak_run_rc == 0 )); then
          # The pinned minimal EDG runner need not have GNU cmp.
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/weak-word-gcc.out /audit-tmp/weak-word-typeid.out
          weak_parity_rc=$?
        fi
      fi
      set -e
    fi
    echo "EDG_C_WEAK_TYPEID_ELF_EXIT=$weak_native_rc"
    echo "EDG_C_WEAK_TYPEID_GCC_EXIT=$weak_gcc_rc"
    echo "EDG_C_WEAK_TYPEID_LINK_EXIT=$weak_link_rc"
    echo "EDG_C_WEAK_TYPEID_RUN_EXIT=$weak_run_rc"
    echo "EDG_C_WEAK_TYPEID_PARITY_EXIT=$weak_parity_rc"
    if ((weak_native_rc != 0)); then
      # The only source is committed public C11; never print private mrustc.
      grep -E '(error\[E|^error:|rejected source|EDG TypeId|invalid)' \
        /audit-tmp/weak-word-typeid-object.log | tail -n 12 |
        sed -E 's@/(workspace|audit-tmp)/[^ :]+@<public-fixture>@g' || true
    fi
  fi
  # Public C11 strong integer plus three weak aggregate definitions forming
  # a source-owned address cycle. Original GCC and selected TypeId ELF must
  # both contain exact symbolic relocations, weak binding and runnable code.
  # Private generated mrustc C remains completely separate.
  if [[ "${EDG_WEAK_AGGREGATE_CYCLE_ORACLE:-0}" == "1" ]]; then
    cycle_src=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_weak_union_aggregate_cycle.c
    cycle_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_weak_union_aggregate_cycle_harness.c
    test -s "$cycle_src" && test -s "$cycle_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver weak_aggregate_to_aggregate_cycles -- --test-threads=1
    ) > /audit-tmp/weak-cycle-typeid-unit.log 2>&1
    cycle_unit_rc=$?
    cycle_typeid_rc=11
    if (( cycle_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/weak-cycle-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$cycle_src"
      ) > /audit-tmp/weak-cycle-typeid-object.log 2>&1
      cycle_typeid_rc=$?
    fi
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$cycle_src" \
      -o /audit-tmp/weak-cycle-gcc.o
    cycle_gcc_rc=$?
    if (( cycle_gcc_rc == 0 && cycle_typeid_rc == 0 )); then
      for obj in /audit-tmp/weak-cycle-gcc.o /audit-tmp/weak-cycle-typeid.o; do
        routine=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*edg_weak_cycle_routine' || true)
        data=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*edg_weak_[abc]([[:space:]]|$)' || true)
        weak=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="WEAK" &&
               $8 ~ /^edg_weak_[abc]$/ {n++} END {print n+0}')
        if [[ "$routine" != 3 || "$data" != 3 || "$weak" != 3 ]]; then
          if [[ "$obj" == *typeid.o ]]; then cycle_typeid_rc=12
          else cycle_gcc_rc=12; fi
        fi
      done
    fi
    cycle_link_rc=11
    cycle_run_rc=11
    cycle_parity_rc=11
    if (( cycle_gcc_rc == 0 && cycle_typeid_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/weak-cycle-gcc.o "$cycle_harness" -o /audit-tmp/weak-cycle-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/weak-cycle-typeid.o "$cycle_harness" -o /audit-tmp/weak-cycle-typeid-exe
      cycle_link_rc=$?
      if (( cycle_link_rc == 0 )); then
        /audit-tmp/weak-cycle-gcc-exe > /audit-tmp/weak-cycle-gcc.out &&
        /audit-tmp/weak-cycle-typeid-exe > /audit-tmp/weak-cycle-typeid.out
        cycle_run_rc=$?
        if (( cycle_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/weak-cycle-gcc.out /audit-tmp/weak-cycle-typeid.out
          cycle_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_WEAK_CYCLE_UNIT_EXIT=$cycle_unit_rc"
    echo "EDG_C_WEAK_CYCLE_ELF_EXIT=$cycle_typeid_rc"
    echo "EDG_C_WEAK_CYCLE_GCC_EXIT=$cycle_gcc_rc"
    echo "EDG_C_WEAK_CYCLE_LINK_EXIT=$cycle_link_rc"
    echo "EDG_C_WEAK_CYCLE_RUN_EXIT=$cycle_run_rc"
    echo "EDG_C_WEAK_CYCLE_PARITY_EXIT=$cycle_parity_rc"
    if (( cycle_unit_rc != 0 || cycle_typeid_rc != 0 )); then
      # Both files originate exclusively from committed public C11 fixture
      # and TypeId tests. Limit output and strip absolute runner paths.
      python3 - /audit-tmp/weak-cycle-typeid-unit.log \
        /audit-tmp/weak-cycle-typeid-object.log <<'PY'
from pathlib import Path
import re,sys
messages=[]
for path in map(Path,sys.argv[1:]):
    if not path.is_file():continue
    for line in path.read_text(errors="replace").splitlines():
        if any(key in line for key in
               ("error[E","error:","FAILED","panicked","test result:")):
            line=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","<public-fixture>",line)
            line=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","?",line)
            messages.append(line.strip()[:180])
for i,line in enumerate(messages[-8:],1):
    print(f"EDG_C_WEAK_CYCLE_DIAG_{i}={line}")
PY
    fi
  fi
  # Public original GNU C11 proof for tentative struct BSS, explicit
  # strong byte-image union and source-owned weak symbolic routine union.
  # No source programs or symbols from the private mrustc TU are exported.
  if [[ "${EDG_STATIC_RECORD_ORACLE:-0}" == "1" ]]; then
    record_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_static_bytes_bss_weak.c
    record_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_static_bytes_bss_weak_harness.c
    test -s "$record_source" && test -s "$record_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver original_c_tentative_struct_and_strong_union_bytes -- --test-threads=1
    ) > /audit-tmp/static-record-unit.log 2>&1
    record_unit_rc=$?
    record_native_rc=11
    if (( record_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/static-record-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$record_source"
      ) > /audit-tmp/static-record-object.log 2>&1
      record_native_rc=$?
    fi
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$record_source" \
      -o /audit-tmp/static-record-gcc.o
    record_gcc_rc=$?
    if (( record_native_rc == 0 && record_gcc_rc == 0 )); then
      for obj in /audit-tmp/static-record-typeid.o /audit-tmp/static-record-gcc.o; do
        bss=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="GLOBAL" && $3==16 &&
               $8=="edg_static_record_bss" {n++} END {print n+0}')
        strong=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="GLOBAL" && $3==40 &&
               $8=="edg_static_record_bytes" {n++} END {print n+0}')
        weak=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="WEAK" && $3==40 &&
               $8=="edg_static_record_weak" {n++} END {print n+0}')
        reloc=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*edg_static_record_fn' || true)
        bss_section=$(readelf -SW "$obj" |
          grep -Ec '[[:space:]]\.bss[[:space:]]+NOBITS' || true)
        if [[ "$bss" != 1 || "$strong" != 1 || "$weak" != 1 ||
              "$reloc" != 1 || "$bss_section" -lt 1 ]]; then
          if [[ "$obj" == *typeid.o ]]; then record_native_rc=12
          else record_gcc_rc=12; fi
        fi
      done
    fi
    record_link_rc=11
    record_run_rc=11
    record_parity_rc=11
    if (( record_native_rc == 0 && record_gcc_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/static-record-gcc.o "$record_harness" -o /audit-tmp/static-record-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/static-record-typeid.o "$record_harness" -o /audit-tmp/static-record-typeid-exe
      record_link_rc=$?
      if (( record_link_rc == 0 )); then
        /audit-tmp/static-record-gcc-exe > /audit-tmp/static-record-gcc.out &&
        /audit-tmp/static-record-typeid-exe > /audit-tmp/static-record-typeid.out
        record_run_rc=$?
        if (( record_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/static-record-gcc.out /audit-tmp/static-record-typeid.out
          record_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_RECORD_BSS_UNIT_EXIT=$record_unit_rc"
    echo "EDG_C_RECORD_BSS_ELF_EXIT=$record_native_rc"
    echo "EDG_C_RECORD_BSS_GCC_EXIT=$record_gcc_rc"
    echo "EDG_C_RECORD_BSS_LINK_EXIT=$record_link_rc"
    echo "EDG_C_RECORD_BSS_RUN_EXIT=$record_run_rc"
    echo "EDG_C_RECORD_BSS_PARITY_EXIT=$record_parity_rc"
    if (( record_unit_rc != 0 || record_native_rc != 0 )); then
      python3 - /audit-tmp/static-record-unit.log /audit-tmp/static-record-object.log <<'PY'
from pathlib import Path
import re,sys
messages=[]
for path in map(Path,sys.argv[1:]):
    if not path.is_file():continue
    for line in path.read_text(errors="replace").splitlines():
        if any(key in line for key in ("error[E","error:","FAILED","panicked","test result:")):
            line=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","<public-fixture>",line)
            line=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","?",line)
            messages.append(line.strip()[:180])
for i,line in enumerate(messages[-8:],1):
    print(f"EDG_C_RECORD_BSS_DIAG_{i}={line}")
PY
    fi
  fi
  # Original public C11 const scalar fixture. The independent GCC object
  # and TypeId object must both retain exact readonly byte images (including
  # const zero), a mutable global, one weak relocation, and runtime parity.
  if [[ "${EDG_C_CONST_STATIC_ORACLE:-0}" == "1" ]]; then
    const_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_const_static_scalar.c
    const_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_const_static_scalar_harness.c
    test -s "$const_source" && test -s "$const_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver source_owned_c_const_scalars -- --test-threads=1
    ) > /audit-tmp/edg-c-const-unit.log 2>&1
    const_unit_rc=$?
    const_native_rc=11
    if (( const_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/edg-c-const-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$const_source"
      ) > /audit-tmp/edg-c-const-native.log 2>&1
      const_native_rc=$?
    fi
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$const_source" \
      -o /audit-tmp/edg-c-const-gcc.o
    const_gcc_rc=$?
    if (( const_gcc_rc == 0 && const_native_rc == 0 )); then
      for obj in /audit-tmp/edg-c-const-gcc.o /audit-tmp/edg-c-const-typeid.o; do
        ro=$(readelf -SW "$obj" | grep -Ec '[[:space:]]\.rodata[[:space:]]+PROGBITS' || true)
        ci32=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="GLOBAL" && $3==4 &&
               $8=="edg_c_const_i32" {n++} END {print n+0}')
        cu64=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="GLOBAL" && $3==8 &&
               $8=="edg_c_const_u64_zero" {n++} END {print n+0}')
        mi32=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="GLOBAL" && $3==4 &&
               $8=="edg_c_mutable_i32" {n++} END {print n+0}')
        wk=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="WEAK" && $3==40 &&
               $8=="edg_c_const_weak_union" {n++} END {print n+0}')
        reloc=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*edg_c_const_routine' || true)
        if [[ "$ro" -lt 1 || "$ci32" != 1 || "$cu64" != 1 ||
              "$mi32" != 1 || "$wk" != 1 || "$reloc" != 1 ]]; then
          if [[ "$obj" == *typeid.o ]]; then const_native_rc=12
          else const_gcc_rc=12; fi
        fi
      done
    fi
    const_link_rc=11
    const_run_rc=11
    const_parity_rc=11
    if (( const_gcc_rc == 0 && const_native_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-const-gcc.o "$const_harness" -o /audit-tmp/edg-c-const-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-const-typeid.o "$const_harness" -o /audit-tmp/edg-c-const-typeid-exe
      const_link_rc=$?
      if (( const_link_rc == 0 )); then
        /audit-tmp/edg-c-const-gcc-exe > /audit-tmp/edg-c-const-gcc.out &&
        /audit-tmp/edg-c-const-typeid-exe > /audit-tmp/edg-c-const-typeid.out
        const_run_rc=$?
        if (( const_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/edg-c-const-gcc.out /audit-tmp/edg-c-const-typeid.out
          const_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_CONST_STATIC_UNIT_EXIT=$const_unit_rc"
    echo "EDG_C_CONST_STATIC_ELF_EXIT=$const_native_rc"
    echo "EDG_C_CONST_STATIC_GCC_EXIT=$const_gcc_rc"
    echo "EDG_C_CONST_STATIC_LINK_EXIT=$const_link_rc"
    echo "EDG_C_CONST_STATIC_RUN_EXIT=$const_run_rc"
    echo "EDG_C_CONST_STATIC_PARITY_EXIT=$const_parity_rc"
    if (( const_unit_rc != 0 || const_native_rc != 0 )); then
      python3 - /audit-tmp/edg-c-const-unit.log /audit-tmp/edg-c-const-native.log <<'PY'
from pathlib import Path
import re,sys
messages=[]
for path in map(Path,sys.argv[1:]):
    if not path.is_file():continue
    for line in path.read_text(errors="replace").splitlines():
        if any(k in line for k in ("error[E","error:","FAILED","panicked","test result:")):
            line=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","<public-fixture>",line)
            line=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","?",line)
            messages.append(line.strip()[:180])
for i,msg in enumerate(messages[-8:],1):
    print(f"EDG_C_CONST_STATIC_DIAG_{i}={msg}")
PY
    fi
  fi
  # Original public GNU C11 byte-global differential. TypeId must retain
  # exact one-byte ELF storage, signed/unsigned narrow loads/stores and
  # source-verified weak routine address, not inflate bytes into I32 data.
  if [[ "${EDG_C_BYTE_STATIC_ORACLE:-0}" == "1" ]]; then
    byte_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_c_byte_static.c
    byte_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_c_byte_static_harness.c
    test -s "$byte_source" && test -s "$byte_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver source_owned_c_byte_globals -- --nocapture --test-threads=1
    ) > /audit-tmp/edg-c-byte-unit.log 2>&1
    byte_unit_rc=$?
    byte_native_rc=11
    if (( byte_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/edg-c-byte-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$byte_source"
      ) > /audit-tmp/edg-c-byte-native.log 2>&1
      byte_native_rc=$?
    fi
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$byte_source" \
      -o /audit-tmp/edg-c-byte-gcc.o
    byte_gcc_rc=$?
    if (( byte_gcc_rc == 0 && byte_native_rc == 0 )); then
      for obj in /audit-tmp/edg-c-byte-gcc.o /audit-tmp/edg-c-byte-typeid.o; do
        byte_symbols=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="GLOBAL" && $3==1 &&
               $8 ~ /^edg_c_byte_(signed|unsigned|zero|const)$/ {n++} END {print n+0}')
        weak=$(readelf -Ws "$obj" |
          awk '$4=="OBJECT" && $5=="WEAK" && $3==40 &&
               $8=="edg_c_byte_weak" {n++} END {print n+0}')
        reloc=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*edg_c_byte_fn' || true)
        bss=$(readelf -SW "$obj" | grep -Ec '[[:space:]]\.bss[[:space:]]+NOBITS' || true)
        rodata=$(readelf -SW "$obj" | grep -Ec '[[:space:]]\.rodata[[:space:]]+PROGBITS' || true)
        if [[ "$byte_symbols" != 4 || "$weak" != 1 || "$reloc" != 1 ||
              "$bss" -lt 1 || "$rodata" -lt 1 ]]; then
          if [[ "$obj" == *typeid.o ]]; then byte_native_rc=12
          else byte_gcc_rc=12; fi
        fi
      done
    fi
    byte_link_rc=11
    byte_run_rc=11
    byte_parity_rc=11
    if (( byte_native_rc == 0 && byte_gcc_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-byte-gcc.o "$byte_harness" -o /audit-tmp/edg-c-byte-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-byte-typeid.o "$byte_harness" -o /audit-tmp/edg-c-byte-typeid-exe
      byte_link_rc=$?
      if (( byte_link_rc == 0 )); then
        /audit-tmp/edg-c-byte-gcc-exe > /audit-tmp/edg-c-byte-gcc.out &&
        /audit-tmp/edg-c-byte-typeid-exe > /audit-tmp/edg-c-byte-typeid.out
        byte_run_rc=$?
        if (( byte_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/edg-c-byte-gcc.out /audit-tmp/edg-c-byte-typeid.out
          byte_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_BYTE_STATIC_UNIT_EXIT=$byte_unit_rc"
    echo "EDG_C_BYTE_STATIC_ELF_EXIT=$byte_native_rc"
    echo "EDG_C_BYTE_STATIC_GCC_EXIT=$byte_gcc_rc"
    echo "EDG_C_BYTE_STATIC_LINK_EXIT=$byte_link_rc"
    echo "EDG_C_BYTE_STATIC_RUN_EXIT=$byte_run_rc"
    echo "EDG_C_BYTE_STATIC_PARITY_EXIT=$byte_parity_rc"
    if (( byte_unit_rc != 0 || byte_native_rc != 0 )); then
      python3 - /audit-tmp/edg-c-byte-unit.log /audit-tmp/edg-c-byte-native.log <<'PY'
from pathlib import Path
import re,sys
messages=[]
for index,path in enumerate(map(Path,sys.argv[1:])):
    if not path.is_file():continue
    lines=path.read_text(errors="replace").splitlines()
    if index==0:
        # This log contains only the public C fixture and Rust unit test.
        # Retain numeric facts, not arbitrary generated-C or Cargo text.
        print(f"EDG_C_BYTE_STATIC_UNIT_LOG_LINES={min(len(lines),999999)}")
        print(f"EDG_C_BYTE_STATIC_UNIT_RUST_ERRORS={min(sum('error:' in s or 'error[E' in s for s in lines),9999)}")
        print(f"EDG_C_BYTE_STATIC_UNIT_PANICS={min(sum('panicked' in s or 'FAILED' in s for s in lines),9999)}")
        stages=[int(m.group(1)) for s in lines
                if (m:=re.search(r'EDG_PUBLIC_BYTE_STAGE=([1-9])',s))]
        print(f"EDG_C_BYTE_STATIC_LAST_STAGE={max(stages,default=0)}")
        for s in lines:
            if "EDG_PUBLIC_BYTE_ERROR=" not in s:continue
            # Only original checked-in public test diagnostics, never
            # generated mrustc C or arbitrary raw compiler output.
            reason=s.split("EDG_PUBLIC_BYTE_ERROR=",1)[1].strip()
            reason=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","source",reason)
            reason=re.sub(r"[^A-Za-z0-9]+","_",reason).strip("_")[:160]
            if reason:
                print(f"EDG_C_BYTE_STATIC_PUBLIC_REASON={reason}")
                break
    for line in lines:
        if any(k in line for k in ("error[E","error:","FAILED","panicked","test result:")):
            line=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","<public-fixture>",line)
            # Keep redacted punctuation in the diagnostic schema accepted by the
            # workflow summary, so source-public Rust failures are visible.
            line=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",line)
            messages.append(line.strip()[:180])
if not messages:
    messages=["no_matching_public_unit_diagnostic"]
for i,msg in enumerate(messages[-8:],1):
    print(f"EDG_C_BYTE_STATIC_DIAG_{i}={msg}")
PY
    fi
  fi
  # Original C11 signed/unsigned/_Bool AL return parity. The checked-in
  # source object is built by the native TypeId backend and GCC separately;
  # an independently compiled GCC harness checks actual function-call ABI
  # and the source-owned nested byte-return calls. No private mrustc input.
  if [[ "${EDG_C_BYTE_STATIC_ORACLE:-0}" == "1" ]]; then
    result_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_c_byte_returns.c
    result_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_c_byte_returns_harness.c
    test -s "$result_source" && test -s "$result_harness"
    byte_result_unit_rc=${local_typedef_rc:-11}
    byte_result_native_rc=11
    byte_result_gcc_rc=11
    byte_result_link_rc=11
    byte_result_run_rc=11
    byte_result_parity_rc=11
    set +e
    if (( byte_result_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/edg-c-byte-result-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$result_source"
      ) > /audit-tmp/edg-c-byte-result-native.log 2>&1
      byte_result_native_rc=$?
    fi
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$result_source" \
      -o /audit-tmp/edg-c-byte-result-gcc.o
    byte_result_gcc_rc=$?
    if (( byte_result_native_rc == 0 && byte_result_gcc_rc == 0 )); then
      for obj in /audit-tmp/edg-c-byte-result-typeid.o /audit-tmp/edg-c-byte-result-gcc.o; do
        symbols=$(readelf -Ws "$obj" | awk '$4=="FUNC" && $5=="GLOBAL" &&
          $8 ~ /^bootstrap_byte_(unsigned|signed|boolean|returns_main|formal_(unsigned|signed|boolean))$/ {n++}
          END {print n+0}')
        if [[ "$symbols" != 7 ]]; then
          if [[ "$obj" == *typeid.o ]]; then byte_result_native_rc=12
          else byte_result_gcc_rc=12; fi
        fi
      done
    fi
    if (( byte_result_native_rc == 0 && byte_result_gcc_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-byte-result-gcc.o "$result_harness" \
        -o /audit-tmp/edg-c-byte-result-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-byte-result-typeid.o "$result_harness" \
        -o /audit-tmp/edg-c-byte-result-typeid-exe
      byte_result_link_rc=$?
      if (( byte_result_link_rc == 0 )); then
        /audit-tmp/edg-c-byte-result-gcc-exe > /audit-tmp/edg-c-byte-result-gcc.out &&
        /audit-tmp/edg-c-byte-result-typeid-exe > /audit-tmp/edg-c-byte-result-typeid.out
        byte_result_run_rc=$?
        if (( byte_result_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/edg-c-byte-result-gcc.out \
            /audit-tmp/edg-c-byte-result-typeid.out
          byte_result_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_BYTE_RESULT_UNIT_EXIT=$byte_result_unit_rc"
    echo "EDG_C_BYTE_RESULT_ELF_EXIT=$byte_result_native_rc"
    echo "EDG_C_BYTE_RESULT_GCC_EXIT=$byte_result_gcc_rc"
    echo "EDG_C_BYTE_RESULT_LINK_EXIT=$byte_result_link_rc"
    echo "EDG_C_BYTE_RESULT_RUN_EXIT=$byte_result_run_rc"
    echo "EDG_C_BYTE_RESULT_PARITY_EXIT=$byte_result_parity_rc"
    if (( byte_result_native_rc != 0 || byte_result_unit_rc != 0 )); then
      python3 - /audit-tmp/edg-c-byte-result-native.log \
        /audit-tmp/edg-c-local-typedef-unit.log <<'PY'
from pathlib import Path
import re,sys
messages=[]
for path in map(Path,sys.argv[1:]):
    if not path.is_file():continue
    for line in path.read_text(errors="replace").splitlines():
        if any(k in line for k in ("error[E","error:","FAILED","panicked","test result:",
            "EDG_PUBLIC_C_BYTE_RETURN_REASON=")):
            line=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",line.strip())
            line=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",line)
            messages.append(line[:180])
for i,message in enumerate(messages[-8:],1):
    print(f"EDG_C_BYTE_RESULT_DIAG_{i}={message}")
PY
    fi
  fi
  # Independently compiled original C11 initialized-struct oracle.
  # Reuse the existing source-owned byte-image/relocation writer; verify
  # both strong and weak C struct records, tentative BSS and routine/data
  # address slots against GCC, never against private mrustc source text.
  if [[ "${EDG_C_RECORD_INIT_ORACLE:-0}" == "1" ]]; then
    struct_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_initialized_struct_elf.c
    struct_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_initialized_struct_elf_harness.c
    test -s "$struct_source" && test -s "$struct_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" test --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        --bin compiler-edg-driver source_owned_c_initialized_structs_share_verified_aggregate_elf_storage \
        -- --nocapture --test-threads=1
    ) > /audit-tmp/edg-c-struct-init-unit.log 2>&1
    struct_unit_rc=$?
    struct_native_rc=11
    if (( struct_unit_rc == 0 )); then
      (
        cd /workspace/edgcpp/build/gcc-release
        CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
          timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
          --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
          -- /audit-tmp/edg-c-struct-init-typeid.o --gcc --c11 --no_strict_gnu \
          --gnu_version "$gnu_number" "$struct_source"
      ) > /audit-tmp/edg-c-struct-init-native.log 2>&1
      struct_native_rc=$?
    fi
    gcc -std=gnu11 -O0 -Wall -Wextra -Werror -c "$struct_source" \
      -o /audit-tmp/edg-c-struct-init-gcc.o
    struct_gcc_rc=$?
    if (( struct_native_rc == 0 && struct_gcc_rc == 0 )); then
      for obj in /audit-tmp/edg-c-struct-init-typeid.o /audit-tmp/edg-c-struct-init-gcc.o; do
        strong=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==32 &&
            $8=="edg_struct_strong" {n++} END {print n+0}')
        weak=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="WEAK" && $3==32 &&
            $8=="edg_struct_weak" {n++} END {print n+0}')
        tentative=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==32 &&
            $8=="edg_struct_tentative" {n++} END {print n+0}')
        pure=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==24 &&
            $8=="edg_struct_pure" {n++} END {print n+0}')
        bss_ref=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==16 &&
            $8=="edg_struct_bss_ref" {n++} END {print n+0}')
        array_strong=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==24 &&
            $8=="edg_array_strong" {n++} END {print n+0}')
        array_weak=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="WEAK" && $3==16 &&
            $8=="edg_array_weak" {n++} END {print n+0}')
        array_bss=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==32 &&
            $8=="edg_array_tentative" {n++} END {print n+0}')
        array_pure=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==32 &&
            $8=="edg_array_pure" {n++} END {print n+0}')
        pointer_bss=$(readelf -Ws "$obj" | awk '$4=="OBJECT" && $5=="GLOBAL" && $3==8 &&
            $8=="edg_ptr_tentative" {n++} END {print n+0}')
        reloc=$(readelf -rW "$obj" |
          grep -cE 'R_X86_64_64.*(edg_struct_(fn|value|tentative)|edg_array_tentative)' || true)
        bss=$(readelf -SW "$obj" | grep -Ec '[[:space:]]\.bss[[:space:]]+NOBITS' || true)
        if [[ "$strong" != 1 || "$weak" != 1 || "$tentative" != 1 ||
              "$pure" != 1 || "$bss_ref" != 1 || "$array_strong" != 1 ||
              "$array_weak" != 1 || "$array_bss" != 1 ||
              "$array_pure" != 1 || "$pointer_bss" != 1 || "$reloc" != 9 || "$bss" -lt 1 ]]; then
          if [[ "$obj" == *typeid.o ]]; then struct_native_rc=12
          else struct_gcc_rc=12; fi
        fi
      done
    fi
    struct_link_rc=11
    struct_run_rc=11
    struct_parity_rc=11
    if (( struct_native_rc == 0 && struct_gcc_rc == 0 )); then
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-struct-init-gcc.o "$struct_harness" -o /audit-tmp/edg-c-struct-init-gcc-exe &&
      gcc -std=gnu11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-c-struct-init-typeid.o "$struct_harness" -o /audit-tmp/edg-c-struct-init-typeid-exe
      struct_link_rc=$?
      if (( struct_link_rc == 0 )); then
        /audit-tmp/edg-c-struct-init-gcc-exe > /audit-tmp/edg-c-struct-init-gcc.out &&
        /audit-tmp/edg-c-struct-init-typeid-exe > /audit-tmp/edg-c-struct-init-typeid.out
        struct_run_rc=$?
        if (( struct_run_rc == 0 )); then
          python3 -c 'from pathlib import Path; import sys; sys.exit(
              0 if Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()
              else 1)' \
            /audit-tmp/edg-c-struct-init-gcc.out /audit-tmp/edg-c-struct-init-typeid.out
          struct_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_STRUCT_INIT_UNIT_EXIT=$struct_unit_rc"
    echo "EDG_C_STRUCT_INIT_ELF_EXIT=$struct_native_rc"
    echo "EDG_C_STRUCT_INIT_GCC_EXIT=$struct_gcc_rc"
    echo "EDG_C_STRUCT_INIT_LINK_EXIT=$struct_link_rc"
    echo "EDG_C_STRUCT_INIT_RUN_EXIT=$struct_run_rc"
    echo "EDG_C_STRUCT_INIT_PARITY_EXIT=$struct_parity_rc"
    # Only the newly checked-in *public* C fixture enters these unit logs.
    # Do not serialize any source names or excerpts from private mrustc C.
    python3 - /audit-tmp/edg-c-struct-init-unit.log /audit-tmp/edg-c-struct-init-native.log <<'PY'
from pathlib import Path
import re,sys
lines=[]
for p in map(Path,sys.argv[1:]):
    if p.is_file():lines.extend(p.read_text(errors="replace").splitlines())
stages=re.findall(r"EDG_PUBLIC_STRUCT_STAGE=(\d+)", "\n".join(lines))
print("EDG_C_STRUCT_INIT_LAST_STAGE="+(stages[-1] if stages else "0"))
reasons=re.findall(r"EDG_PUBLIC_STRUCT_PARTITION_REASON=([0-9]{1,4})",
                   "\n".join(lines))
print("EDG_C_STRUCT_INIT_PARTITION_REASON="+(
      reasons[-1] if reasons else "0"))
for line in lines:
    if "EDG_PUBLIC_STRUCT_COUNTS " in line:
        nums=re.search(r"EDG_PUBLIC_STRUCT_COUNTS owners=(\d+) images=(\d+) int=(\d+) materializations=(\d+) arrays=(\d+) relocs=(\d+)",line)
        if nums:print("EDG_C_STRUCT_INIT_PUBLIC_COUNTS="+"_".join(nums.groups()))
        break
errors=[]
for line in lines:
    if any(k in line for k in ("EDG_PUBLIC_STRUCT_ERROR=", "error[E", "error:", "panicked", "FAILED", "test result:")):
        msg=line.split("EDG_PUBLIC_STRUCT_ERROR=",1)[-1].strip()
        msg=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",msg)
        msg=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",msg)
        if msg:errors.append(msg[:180])
for i,msg in enumerate(errors[-8:],1):
    print(f"EDG_C_STRUCT_INIT_DIAG_{i}={msg}")
# The trace belongs only to the checked-in public C fixture and the
# public Rust test. A bounded sanitized tail makes an unexpected panic
# actionable even if it does not use Cargo's normal 'error:' spelling.
source=Path(sys.argv[1])
if source.is_file():
    tail=source.read_text(errors="replace").splitlines()[-16:]
    for i,line in enumerate(tail,1):
        msg=re.sub(r"/(?:workspace|audit-tmp)/[^ :]+","public_fixture",line.strip())
        msg=re.sub(r"[^A-Za-z0-9 _.,=:/()><-]","_",msg)
        if msg:print(f"EDG_C_STRUCT_INIT_TRACE_{i}={msg[:180]}")
PY
  fi
  # An actual ELF+GCC executable oracle, not just a bridge snapshot.
  # These two original C11 source files are public, checked-in compiler
  # fixtures and do not depend on/print the private generated mrustc TU.
  if (( typeid_check_rc == 0 )); then
    literal_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_literal_union_elf.c
    literal_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_literal_union_elf_harness.c
    test -s "$literal_source" && test -s "$literal_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        -- /audit-tmp/edg-static-literal-native.o --gcc --c11 \
        --no_strict_gnu --gnu_version "$gnu_number" "$literal_source"
    ) > /audit-tmp/edg-static-literal-native.log 2>&1
    native_rc=$?
    set -e
    if (( native_rc == 0 )); then
      readelf -h /audit-tmp/edg-static-literal-native.o >/dev/null
      readelf -rW /audit-tmp/edg-static-literal-native.o \
        > /audit-tmp/edg-static-literal-native-relocs.txt
      reloc_count=$(grep -c 'R_X86_64_64' \
        /audit-tmp/edg-static-literal-native-relocs.txt || true)
      echo "EDG_C_LITERAL_NATIVE_ELF_RELOCATIONS=$reloc_count"
      if [[ "$reloc_count" != "5" ]]; then native_rc=11; fi
    fi
    echo "EDG_C_LITERAL_TYPEID_ELF_EXIT=$native_rc"
    set +e
    gcc -std=c11 -O0 -c "$literal_source" -o /audit-tmp/edg-static-literal-gcc.o &&
      gcc -std=c11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-static-literal-gcc.o "$literal_harness" \
        -o /audit-tmp/edg-static-literal-gcc-exe &&
      /audit-tmp/edg-static-literal-gcc-exe \
        > /audit-tmp/edg-static-literal-gcc.out
    gcc_oracle_rc=$?
    # The minimal EDG runner image may not have coreutils cmp. Keep
    # link, execution and byte comparison as independent numeric oracles.
    native_link_rc=11
    native_run_rc=11
    native_parity_rc=11
    if (( native_rc == 0 )); then
      gcc -std=c11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-static-literal-native.o "$literal_harness" \
        -o /audit-tmp/edg-static-literal-native-exe
      native_link_rc=$?
      if (( native_link_rc == 0 )); then
        /audit-tmp/edg-static-literal-native-exe \
          > /audit-tmp/edg-static-literal-native.out \
          2> /audit-tmp/edg-static-literal-native.stderr
        native_run_rc=$?
        if (( native_run_rc == 0 && gcc_oracle_rc == 0 )); then
          python3 - /audit-tmp/edg-static-literal-gcc.out \
              /audit-tmp/edg-static-literal-native.out <<'PY'
from pathlib import Path
import sys
expected=Path(sys.argv[1]).read_bytes()
observed=Path(sys.argv[2]).read_bytes()
if expected!=b"STATIC_LITERAL_ELF_PARITY_OK\n" or observed!=expected:
    raise SystemExit(1)
PY
          native_parity_rc=$?
        fi
      fi
    fi
    echo "EDG_C_LITERAL_NATIVE_LINK_EXIT=$native_link_rc"
    echo "EDG_C_LITERAL_NATIVE_RUN_EXIT=$native_run_rc"
    echo "EDG_C_LITERAL_NATIVE_PARITY_EXIT=$native_parity_rc"
    if (( native_link_rc == 0 && native_run_rc == 0 && native_parity_rc == 0 )); then
      native_oracle_rc=0
    else
      native_oracle_rc=1
    fi
    set -e
    echo "EDG_C_LITERAL_GCC_ELF_ORACLE_EXIT=$gcc_oracle_rc"
    echo "EDG_C_LITERAL_NATIVE_ELF_ORACLE_EXIT=$native_oracle_rc"
    if (( native_rc != 0 || gcc_oracle_rc != 0 || native_oracle_rc != 0 )); then
      python3 - /audit-tmp/edg-static-literal-native.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if "error[E" in line or "error:" in line or "rejected source" in line]
for i,line in enumerate(selected[-8:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_LITERAL_NATIVE_DIAG_{i}={msg[:230]}")
PY
    fi

    # A second public C11 original-source acceptance test: one I32 global
    # plus one literal-bearing union, with a real global load in a function.
    # The consumer must prove the *entire* disjoint ownership partition.
    mixed_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_literal_mixed_elf.c
    mixed_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_literal_mixed_elf_harness.c
    test -s "$mixed_source" && test -s "$mixed_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        -- /audit-tmp/edg-static-literal-mixed-native.o --gcc --c11 \
        --no_strict_gnu --gnu_version "$gnu_number" "$mixed_source"
    ) > /audit-tmp/edg-static-literal-mixed-native.log 2>&1
    mixed_native_rc=$?
    set -e
    if (( mixed_native_rc == 0 )); then
      readelf -h /audit-tmp/edg-static-literal-mixed-native.o >/dev/null
      readelf -rW /audit-tmp/edg-static-literal-mixed-native.o \
        > /audit-tmp/edg-static-literal-mixed-native-relocs.txt
      mixed_reloc_count=$(python3 - /audit-tmp/edg-static-literal-mixed-native-relocs.txt <<'PY'
from pathlib import Path
import re,sys
section=""
count=0
for line in Path(sys.argv[1]).read_text().splitlines():
    match=re.match(r"Relocation section '([^']+)'",line)
    if match:
        section=match.group(1)
    elif "R_X86_64_64" in line and (
        section.startswith(".rela.data") or section.startswith(".rela.rodata")):
        count+=1
print(count)
PY
)
      echo "EDG_C_LITERAL_MIXED_RELOCATIONS=$mixed_reloc_count"
      if [[ "$mixed_reloc_count" != "2" ]]; then mixed_native_rc=11; fi
    fi
    echo "EDG_C_LITERAL_MIXED_TYPEID_ELF_EXIT=$mixed_native_rc"
    set +e
    gcc -std=c11 -O0 -c "$mixed_source" -o /audit-tmp/edg-static-literal-mixed-gcc.o &&
      gcc -std=c11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-static-literal-mixed-gcc.o "$mixed_harness" \
        -o /audit-tmp/edg-static-literal-mixed-gcc-exe &&
      /audit-tmp/edg-static-literal-mixed-gcc-exe \
        > /audit-tmp/edg-static-literal-mixed-gcc.out
    mixed_gcc_rc=$?
    mixed_link_rc=11
    mixed_run_rc=11
    mixed_parity_rc=11
    if (( mixed_native_rc == 0 )); then
      gcc -std=c11 -O0 -fno-pie -no-pie \
        /audit-tmp/edg-static-literal-mixed-native.o "$mixed_harness" \
        -o /audit-tmp/edg-static-literal-mixed-native-exe
      mixed_link_rc=$?
      if (( mixed_link_rc == 0 )); then
        /audit-tmp/edg-static-literal-mixed-native-exe \
          > /audit-tmp/edg-static-literal-mixed-native.out \
          2> /audit-tmp/edg-static-literal-mixed-native.stderr
        mixed_run_rc=$?
        if (( mixed_run_rc == 0 && mixed_gcc_rc == 0 )); then
          python3 - /audit-tmp/edg-static-literal-mixed-gcc.out \
              /audit-tmp/edg-static-literal-mixed-native.out <<'PY'
from pathlib import Path
import sys
expected=Path(sys.argv[1]).read_bytes()
observed=Path(sys.argv[2]).read_bytes()
if expected!=b"MIXED_STATIC_LITERAL_ELF_PARITY_OK\n" or observed!=expected:
    raise SystemExit(1)
PY
          mixed_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_LITERAL_MIXED_GCC_EXIT=$mixed_gcc_rc"
    echo "EDG_C_LITERAL_MIXED_LINK_EXIT=$mixed_link_rc"
    echo "EDG_C_LITERAL_MIXED_RUN_EXIT=$mixed_run_rc"
    echo "EDG_C_LITERAL_MIXED_PARITY_EXIT=$mixed_parity_rc"
    if (( mixed_native_rc != 0 || mixed_gcc_rc != 0 ||
          mixed_link_rc != 0 || mixed_run_rc != 0 || mixed_parity_rc != 0 )); then
      python3 - /audit-tmp/edg-static-literal-mixed-native.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if any(
    needle in line for needle in ("error[E","error:","rejected source","panicked"))]
for i,line in enumerate(selected[-8:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_LITERAL_MIXED_DIAG_{i}={msg[:230]}")
PY
    fi
    # The real 32-byte union's rejected leaf is a ck_address carrying an
    # explicitly cast original pointer in a C integer word slot. Assert
    # native symbolic R_X86_64_64 + independently GCC-linked runtime parity
    # on this public reproduction; no pointer bits or mrustc text are logged.
    word_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_integer_literal_address.c
    word_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_integer_literal_address_harness.c
    test -s "$word_source" && test -s "$word_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        -- /audit-tmp/edg-static-literal-word-native.o --gcc --c11 \
        --no_strict_gnu --gnu_version "$gnu_number" "$word_source"
    ) > /audit-tmp/edg-static-literal-word-native.log 2>&1
    word_native_rc=$?
    word_reloc_count=0
    if (( word_native_rc == 0 )); then
      readelf -rW /audit-tmp/edg-static-literal-word-native.o \
        > /audit-tmp/edg-static-literal-word-relocs.txt
      word_reloc_count=$(python3 - /audit-tmp/edg-static-literal-word-relocs.txt <<'PY'
from pathlib import Path
import re,sys
section=""
count=0
for line in Path(sys.argv[1]).read_text().splitlines():
    match=re.match(r"Relocation section '([^']+)'",line)
    if match:section=match.group(1)
    elif "R_X86_64_64" in line and (
        section.startswith(".rela.data") or section.startswith(".rela.rodata")):
        count+=1
print(count)
PY
)
      if [[ "$word_reloc_count" != "1" ]]; then word_native_rc=11; fi
    fi
    gcc -std=c11 -O0 -Wall -Wextra -Werror -c "$word_source" \
      -o /audit-tmp/edg-static-literal-word-gcc.o &&
      gcc -std=c11 -O0 -Wall -Wextra -Werror -no-pie \
        /audit-tmp/edg-static-literal-word-gcc.o "$word_harness" \
        -o /audit-tmp/edg-static-literal-word-gcc-exe &&
      /audit-tmp/edg-static-literal-word-gcc-exe \
        > /audit-tmp/edg-static-literal-word-gcc.out
    word_gcc_rc=$?
    word_native_link_rc=11
    word_native_run_rc=11
    word_parity_rc=11
    if (( word_native_rc == 0 )); then
      gcc -std=c11 -O0 -Wall -Wextra -Werror -no-pie \
        /audit-tmp/edg-static-literal-word-native.o "$word_harness" \
        -o /audit-tmp/edg-static-literal-word-native-exe
      word_native_link_rc=$?
      if (( word_native_link_rc == 0 )); then
        /audit-tmp/edg-static-literal-word-native-exe \
          > /audit-tmp/edg-static-literal-word-native.out
        word_native_run_rc=$?
        if (( word_native_run_rc == 0 && word_gcc_rc == 0 )); then
          python3 - /audit-tmp/edg-static-literal-word-gcc.out \
              /audit-tmp/edg-static-literal-word-native.out <<'PY'
from pathlib import Path
import sys
expected=Path(sys.argv[1]).read_bytes()
observed=Path(sys.argv[2]).read_bytes()
if expected!=b"STATIC_INTEGER_ADDRESS_PARITY_OK\n" or expected!=observed:
    raise SystemExit(1)
PY
          word_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_LITERAL_WORD_ELF_EXIT=$word_native_rc"
    echo "EDG_C_LITERAL_WORD_DATA_RELOCATIONS=$word_reloc_count"
    echo "EDG_C_LITERAL_WORD_GCC_EXIT=$word_gcc_rc"
    echo "EDG_C_LITERAL_WORD_LINK_EXIT=$word_native_link_rc"
    echo "EDG_C_LITERAL_WORD_RUN_EXIT=$word_native_run_rc"
    echo "EDG_C_LITERAL_WORD_PARITY_EXIT=$word_parity_rc"
    if (( word_native_rc != 0 || word_gcc_rc != 0 ||
          word_native_link_rc != 0 || word_native_run_rc != 0 ||
          word_parity_rc != 0 )); then
      python3 - /audit-tmp/edg-static-literal-word-native.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if any(
    needle in line for needle in ("error[E","error:","rejected source","panicked"))]
for i,line in enumerate(selected[-8:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_LITERAL_WORD_DIAG_{i}={msg[:230]}")
PY
    fi
    # Actual EDG routine-address source family: one R_X86_64_64 data
    # relocation must name the original C function in native ELF.
    routine_source=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_routine_union_elf.c
    routine_harness=/audit-tmp/compiler-literal-candidate/tools/edg-driver/tests/fixtures/bootstrap_routine_union_elf_harness.c
    test -s "$routine_source" && test -s "$routine_harness"
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      CARGO_TARGET_DIR=/workspace/edgcpp-rust/target \
        timeout --signal=TERM --kill-after=10s 8m cargo --config "$edg_patch" run --quiet \
        --manifest-path /audit-tmp/compiler-literal-candidate/tools/edg-driver/Cargo.toml \
        -- /audit-tmp/edg-static-routine-native.o --gcc --c11 \
        --no_strict_gnu --gnu_version "$gnu_number" "$routine_source"
    ) > /audit-tmp/edg-static-routine-native.log 2>&1
    routine_native_rc=$?
    routine_reloc_count=0
    if (( routine_native_rc == 0 )); then
      readelf -rW /audit-tmp/edg-static-routine-native.o \
        > /audit-tmp/edg-static-routine-relocs.txt
      routine_reloc_count=$(python3 - /audit-tmp/edg-static-routine-relocs.txt <<'PY'
from pathlib import Path
import re,sys
section=""
count=0
for line in Path(sys.argv[1]).read_text().splitlines():
    match=re.match(r"Relocation section '([^']+)'",line)
    if match:section=match.group(1)
    elif "R_X86_64_64" in line and (
        section.startswith(".rela.data") or section.startswith(".rela.rodata")):
        count+=1
print(count)
PY
)
      if [[ "$routine_reloc_count" != "1" ]]; then routine_native_rc=11; fi
    fi
    gcc -std=c11 -O0 -Wall -Wextra -Werror -c "$routine_source" \
      -o /audit-tmp/edg-static-routine-gcc.o &&
      gcc -std=c11 -O0 -Wall -Wextra -Werror -no-pie \
        /audit-tmp/edg-static-routine-gcc.o "$routine_harness" \
        -o /audit-tmp/edg-static-routine-gcc-exe &&
      /audit-tmp/edg-static-routine-gcc-exe \
        > /audit-tmp/edg-static-routine-gcc.out
    routine_gcc_rc=$?
    routine_native_link_rc=11
    routine_native_run_rc=11
    routine_parity_rc=11
    if (( routine_native_rc == 0 )); then
      gcc -std=c11 -O0 -Wall -Wextra -Werror -no-pie \
        /audit-tmp/edg-static-routine-native.o "$routine_harness" \
        -o /audit-tmp/edg-static-routine-native-exe
      routine_native_link_rc=$?
      if (( routine_native_link_rc == 0 )); then
        /audit-tmp/edg-static-routine-native-exe \
          > /audit-tmp/edg-static-routine-native.out
        routine_native_run_rc=$?
        if (( routine_native_run_rc == 0 && routine_gcc_rc == 0 )); then
          python3 - /audit-tmp/edg-static-routine-gcc.out \
              /audit-tmp/edg-static-routine-native.out <<'PY'
from pathlib import Path
import sys
expected=Path(sys.argv[1]).read_bytes()
observed=Path(sys.argv[2]).read_bytes()
if expected!=b"STATIC_ROUTINE_ADDRESS_PARITY_OK\n" or expected!=observed:
    raise SystemExit(1)
PY
          routine_parity_rc=$?
        fi
      fi
    fi
    set -e
    echo "EDG_C_ROUTINE_ELF_EXIT=$routine_native_rc"
    echo "EDG_C_ROUTINE_DATA_RELOCATIONS=$routine_reloc_count"
    echo "EDG_C_ROUTINE_GCC_EXIT=$routine_gcc_rc"
    echo "EDG_C_ROUTINE_LINK_EXIT=$routine_native_link_rc"
    echo "EDG_C_ROUTINE_RUN_EXIT=$routine_native_run_rc"
    echo "EDG_C_ROUTINE_PARITY_EXIT=$routine_parity_rc"
    if (( routine_native_rc != 0 || routine_gcc_rc != 0 ||
          routine_native_link_rc != 0 || routine_native_run_rc != 0 ||
          routine_parity_rc != 0 )); then
      python3 - /audit-tmp/edg-static-routine-native.log <<'PY'
from pathlib import Path
import re,sys
lines=Path(sys.argv[1]).read_text(errors="replace").splitlines()
selected=[line for line in lines if any(
    needle in line for needle in ("error[E","error:","rejected source","panicked"))]
for i,line in enumerate(selected[-8:],1):
    msg=re.sub(r"/workspace/[^ :]+","<public-path>",line.strip())
    msg=re.sub(r"[^A-Za-z0-9 _./:><()\[\],=-]","?",msg)
    print(f"EDG_C_ROUTINE_DIAG_{i}={msg[:230]}")
PY
    fi
  else
    echo "EDG_C_LITERAL_TYPEID_ELF_EXIT=skipped_no_compiler"
    echo "EDG_C_LITERAL_GCC_ELF_ORACLE_EXIT=skipped_no_compiler"
    echo "EDG_C_LITERAL_NATIVE_ELF_ORACLE_EXIT=skipped_no_compiler"
  fi
fi

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
  grep -m 1 '^EDG_PROBE_GLOBALS ' "$logfile" || true
  grep -m 1 '^EDG_PROBE_STATIC_OWNERS ' "$logfile" || true
  grep -m 16 '^EDG_PROBE_MISSING_WEAK_TYPE ' "$logfile" || true
  grep -m 1 '^EDG_PROBE_MISSING_WEAK_TYPE_GROUPS ' "$logfile" || true
  grep -m 16 '^EDG_PROBE_STATIC_KIND ' "$logfile" || true
  grep -m 1 '^EDG_PROBE_STATIC_KIND_GROUPS ' "$logfile" || true
  grep -m 16 '^EDG_PROBE_DATA_TARGET ' "$logfile" || true
  grep -m 1 '^EDG_PROBE_DATA_TARGET_GROUPS ' "$logfile" || true
  grep -m 1 '^EDG_PROBE_STATIC_LITERALS ' "$logfile" || true
  grep -m 1 '^EDG_PROBE_STATIC_ROUTINES ' "$logfile" || true
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

# Bound and validate all fields of the opt-in native C union constant-tree
# diagnostic. Never print source/identifier, diagnostic addresses or logs.
python3 - /audit-tmp/edg-union-image-failures.txt <<'PY'
from pathlib import Path
import re,sys
fields=("type_size","type_align","root_kind","first_kind",
        "second_kind","third_kind","first_child_kind",
        "child_count_bounded","root_dynamic","first_address_kind",
        "first_designator")
n=0
diagnostics=Path(sys.argv[1])
for line in (diagnostics.read_text(errors="replace").splitlines()
             if diagnostics.is_file() else []):
    prefix="EDG_C_UNION_IMAGE_DIAG "
    if not line.startswith(prefix):
        continue
    entries=line[len(prefix):].split()
    if len(entries)!=len(fields):
        continue
    out=[]
    for entry,key in zip(entries,fields):
        match=re.fullmatch(re.escape(key)+r"=([0-9]{1,10})",entry)
        if match is None:
            break
        out.append(match.group(1))
    else:
        print(prefix+" ".join(f"{key}={value}"
                           for key,value in zip(fields,out)))
        n+=1
        if n==4:
            break
PY

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
if [[ "${EDG_TYPEID_UNIT_REGRESSION:-0}" == "1" ]]; then
  # Run only the new public, deterministic proof regression. Cargo can reuse
  # the just-built EDG/native and compiler workspace artifacts.
  set +e
  timeout --signal=TERM --kill-after=10s 3m cargo test --quiet \
    --manifest-path compiler/tools/edg-driver/Cargo.toml \
    --bin compiler-edg-driver \
    mixed_c_static_data_and_source_proven_external_imports_are_separate \
    > /audit-tmp/edg-typeid-mixed-global-regression.log 2>&1
  unit_rc=$?
  set -e
  echo "EDG_TYPEID_MIXED_GLOBAL_UNIT_EXIT=$unit_rc"
  if (( unit_rc != 0 )); then
    # This test contains public inline fixtures only, never generated input.
    grep -E '^error|^test result:|^test .*FAILED|panicked at' \
      /audit-tmp/edg-typeid-mixed-global-regression.log | tail -n 8 | \
      sed 's/^/EDG_TYPEID_MIXED_GLOBAL_UNIT_DIAG=/' || true
  fi
fi
native=/workspace/compiler/tools/edg-driver/target/debug/compiler-edg-driver
test -x "$native"
# Public, independently compiled SysV MEMORY argument reproducer.
# Keep the call and definition separate to detect one-sided ABI mistakes.
probe=/audit-tmp/edg-memory-arg
mkdir -p "$probe"
cat > "$probe/caller.c" <<'C'
struct words { unsigned long a,b,c,d; };
extern unsigned long take_words(struct words x, unsigned long tail);
unsigned long call_words(unsigned long n) {
    struct words x;
    x.a=n; x.b=n+1; x.c=n+2; x.d=n+3;
    return take_words(x, 13);
}
C
cat > "$probe/callee.c" <<'C'
struct words { unsigned long a,b,c,d; };
unsigned long take_words(struct words x, unsigned long tail) {
    x.a += 1;
    return x.a+2*x.b+3*x.c+4*x.d+tail;
}
C
cat > "$probe/main.c" <<'C'
extern unsigned long call_words(unsigned long);
int main(void) { return call_words(5)!=84; }
C
# 6 + 2*6 + 3*7 + 4*8 + 13 = 84.
gcc -std=c11 -c "$probe/caller.c" -o "$probe/gcc-caller.o"
gcc -std=c11 -c "$probe/callee.c" -o "$probe/gcc-callee.o"
gcc -std=c11 "$probe/main.c" "$probe/gcc-caller.o" "$probe/gcc-callee.o" -o "$probe/gcc-only"
set +e
"$probe/gcc-only"
echo "EDG_C_MEMORY_ARG_GCC_EXIT=$?"
for side in caller callee; do
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=10s 90s "$native" "$probe/native-$side.o" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$probe/$side.c"
  ) > "$probe/$side.log" 2>&1
  rc=$?
  echo "EDG_C_MEMORY_ARG_${side^^}_COMPILE_EXIT=$rc"
  if (( rc != 0 )); then
    # This probe is public source, not the real private generated C.
    grep -m 1 'rejected source:' "$probe/$side.log" | cut -c 1-500 |
      sed "s/^/EDG_C_MEMORY_ARG_${side^^}_REJECTION=/" || true
  fi
  if (( rc == 0 )); then
    other=caller
    if [[ "$side" == caller ]]; then other=callee; fi
    gcc -std=c11 "$probe/main.c" "$probe/native-$side.o" "$probe/gcc-$other.o" -o "$probe/mixed-$side" > "$probe/link-$side.log" 2>&1
    link_rc=$?
    echo "EDG_C_MEMORY_ARG_${side^^}_LINK_EXIT=$link_rc"
    if (( link_rc == 0 )); then
      "$probe/mixed-$side"
      echo "EDG_C_MEMORY_ARG_${side^^}_EXEC_EXIT=$?"
    fi
  fi
done
set -e
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

# Re-export ONLY the compiler's fixed numeric code and counters. Do not
# export any real generated-C source, symbol names, paths, or raw diagnostics.
if [[ "${EDG_C_MIXED_GLOBAL_PROBE:-0}" == "1" ]]; then
  python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
fields=("reason","string_literals","literal_objects","literal_relocs",
        "routine_relocs","data_relocs","dynamic","initializer_exprs",
        "scope_dynamic","init_routines","runtime_links","lowered_startups",
        "lifetimes","local_static","fp_images")
prefix="EDG_C_MIXED_PROOF "
matches=[]
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if not line.startswith(prefix): continue
    parts=line[len(prefix):].split()
    if len(parts)!=len(fields): continue
    decoded=[]
    for key,entry in zip(fields,parts):
        m=re.fullmatch(re.escape(key)+r"=([0-9]{1,10})",entry)
        if not m:break
        decoded.append(m.group(1))
    else:
        matches.append(decoded)
if len(matches)==1:
    print("EDG_C_REAL_MIXED_PROOF "+" ".join(
        f"{field}={value}" for field,value in zip(fields,matches[0])))
else:
    print("EDG_C_REAL_MIXED_PROOF_PRESENT=0")
PY
  # A source-independent, bounded aggregate of rejected original/canonical
  # EDG type categories. No TypeIds, names, paths or initializer bytes.
  # A successful complete source-owned C static partition is a durable
  # result even when lowering the first real mrustc function fails later.
  # All C body evidence is bounded numeric EDG semantic categories; the
  # original function name, native symbol ID, path and error remain private.
  python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
schemas={
    "EDG_C_MIXED_PROOF_COMPLETE":(
        "EDG_C_REAL_MIXED_COMPLETE",
        ("integer","aggregate","owners","literal_objects","routine_relocs","data_relocs"),1),
    "EDG_C_BODY_FAILURE":(
        "EDG_C_REAL_BODY_FAILURE",
        ("class","prior","locals","statements","expressions","controls"),1),
    "EDG_C_BODY_STAGE":(
        "EDG_C_REAL_BODY_STAGE",("stage","step"),1),
    "EDG_C_BODY_SCALAR_SITE":(
        "EDG_C_REAL_BODY_SCALAR_SITE",
        ("site","root","kind","expr","op"),1),
    "EDG_C_BODY_CALL_ARG":(
        "EDG_C_REAL_BODY_CALL_ARG",
        ("index","root","kind","bytes","align","vararg","class"),1),
    "EDG_C_BODY_STMT_SITE":(
        "EDG_C_REAL_BODY_STMT_SITE",
        ("kind","expr","op","children","depth"),1),
    "EDG_C_BODY_ASSIGN_SITE":(
        "EDG_C_REAL_BODY_ASSIGN_SITE",
        ("step","lhs","lhsop","lhstype","lhscan",
         "base","baseop","basetype","basescan",
         "rhs","rhsop","rhstype","rhscan"),1),
    "EDG_C_BODY_RESULT_CANON":(
        "EDG_C_REAL_BODY_RESULT_CANON",
        ("root","kind","bytes","align"),1),
    "EDG_C_BODY_STMT_KIND":(
        "EDG_C_REAL_BODY_STMT_KIND",("kind","count"),24),
    "EDG_C_BODY_EXPR_KIND":(
        "EDG_C_REAL_BODY_EXPR_KIND",("kind","op","count"),32),
    "EDG_C_BODY_LOCAL_KIND":(
        "EDG_C_REAL_BODY_LOCAL_KIND",("kind","static","count"),24),
    "EDG_C_BODY_LOCAL_CANON":(
        "EDG_C_REAL_BODY_LOCAL_CANON",
        ("root","kind","aliases","qualifiers","refs","bytes",
         "align","admit","count"),24),
    "EDG_C_BODY_PARAM_KIND":(
        "EDG_C_REAL_BODY_PARAM_KIND",("kind","role","count"),24),
    "EDG_C_BODY_PARAM_CANON":(
        "EDG_C_REAL_BODY_PARAM_CANON",
        ("root","kind","role","aliases","qualifiers","refs","bits",
         "signed","boolean","bytes","align","pointee","count"),24),
    "EDG_C_BODY_PARAM_FIELD":(
        "EDG_C_REAL_BODY_PARAM_FIELD",
        ("bytes","align","members","position","offset","kind","bits",
         "width","field_align","bit_offset","bit_width","count"),32),
}
counts={key:0 for key in schemas}
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    for key,(dest,fields,maximum) in schemas.items():
        if not line.startswith(key+" "):continue
        words=line[len(key)+1:].split()
        if len(words)!=len(fields):continue
        matches=[re.fullmatch(re.escape(k)+r"=([0-9]{1,10})",v)
                 for k,v in zip(fields,words)]
        if any(v is None for v in matches):continue
        if counts[key]>=maximum:continue
        print(dest+" "+" ".join(
            f"{field}={m.group(1)}" for field,m in zip(fields,matches)))
        counts[key]+=1
PY
  python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
keys=("root_raw","root_kind","raw","kind","role","aliases","count")
prefix="EDG_C_MIXED_TYPE_SHAPE "
sent=0
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if not line.startswith(prefix):continue
    words=line[len(prefix):].split()
    if len(words)!=len(keys):continue
    matches=[re.fullmatch(re.escape(k)+r"=([0-9]{1,10})",v)
             for k,v in zip(keys,words)]
    if any(v is None for v in matches):continue
    if sent>=16:break
    print("EDG_C_REAL_MIXED_TYPE_SHAPE "+" ".join(
        f"{k}={v.group(1)}" for k,v in zip(keys,matches)))
    sent+=1
PY
  # This fires only when the compiler encounters an unsupported storage
  # category. The exact native kind, normalized category and layout are
  # numeric target ABI facts, not source-specific symbol identities.
  python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
keys=("raw","kind","aliases","bytes","alignment")
prefix="EDG_C_MIXED_STORAGE_KIND "
count=0
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if not line.startswith(prefix):continue
    parts=line[len(prefix):].split()
    if len(parts)!=len(keys):continue
    matches=[re.fullmatch(re.escape(k)+r"=([0-9]{1,10})",v)
             for k,v in zip(keys,parts)]
    if any(v is None for v in matches):continue
    if count>=8:break
    print("EDG_C_REAL_MIXED_STORAGE_KIND "+" ".join(
        f"{k}={v.group(1)}" for k,v in zip(keys,matches)))
    count+=1
PY
  python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
keys=("integer","aggregate","owners","materializations","integer_images")
prefix="EDG_C_MIXED_PARTITION "
records=[]
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if not line.startswith(prefix):continue
    words=line[len(prefix):].split()
    if len(words)!=len(keys):continue
    values=[]
    for key,word in zip(keys,words):
        found=re.fullmatch(re.escape(key)+r"=([0-9]{1,10})",word)
        if not found:break
        values.append(found.group(1))
    else:records.append(values)
if len(records)==1:
    print("EDG_C_REAL_MIXED_PARTITION "+" ".join(
        f"{key}={val}" for key,val in zip(keys,records[0])))
PY
  # The compiler emits this only for the first unsupported qualified or
  # narrow source integer. No source names, TypeIds or bytes are exported.
  python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
fields=("bits","readonly","c","explicit")
prefix="EDG_C_STATIC_SCALAR_REJECT "
found=[]
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if not line.startswith(prefix):continue
    cells=line[len(prefix):].split()
    if len(cells)!=len(fields):continue
    vals=[]
    for field,cell in zip(fields,cells):
        match=re.fullmatch(re.escape(field)+r"=([0-9]{1,3})",cell)
        if match is None:break
        vals.append(match.group(1))
    else:found.append(vals)
if len(found)==1:
    print("EDG_C_REAL_STATIC_SCALAR_REJECT "+" ".join(
        f"{key}={val}" for key,val in zip(fields,found[0])))
PY
fi

# The original generated C can contain private symbols/source paths.
# Classify the *native adapter's rejection message* with an allowlist of
# hard-coded categories. Never print, hash or copy the raw error line.
python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
data = Path(sys.argv[1]).read_text(errors="replace")
print("EDG_C_TYPEID_DIAGNOSTIC_COUNTS "
      + " ".join(f"{word}={len(re.findall(re.escape(word), data, re.I))}"
                 for word in ("error", "unsupported", "reject", "invalid", "panic")))
markers = {
    "EDG -> compiler-ir rejected source:": "typeid-adapter",
    "EDG bridge:": "bridge-callback",
    "EDG compilation failed with status": "frontend-diagnostic",
}
line = next((line for line in data.splitlines()
             if any(marker in line for marker in markers)), "")
if not line:
    stage = "no-adapter-rejection"
    reason = ""
else:
    stage = next(value for marker,value in markers.items() if marker in line)
    reason = line.split(next(marker for marker in markers if marker in line),1)[-1].lower()
# The rejection class is a constant enum, not the source-dependent message.
if "native global" in reason and "not a proven imported data declaration" in reason:
    classification = "global-definition-not-import"
elif "static literal/object relocation events lack a complete c ownership proof" in reason:
    classification = "global-literal-source-closure"
elif "global" in reason and ("import" in reason or "declaration" in reason):
    classification = "global-import-or-storage-admission"
elif "global" in reason and ("lifetime" in reason or "initializer" in reason):
    classification = "global-lifetime-or-initializer"
elif "source definition" in reason or "statement" in reason or "nested control flow" in reason:
    classification = "function-body-or-control-flow"
elif "expression" in reason or "operator" in reason or "conversion" in reason:
    classification = "expression-or-conversion"
elif "aggregate" in reason or "record" in reason or "class" in reason:
    classification = "aggregate-or-class-abi"
elif "parameter" in reason or "variadic" in reason or "signature" in reason or "call" in reason:
    classification = "routine-or-call-abi"
elif "optimizer" in reason or "machine" in reason or "ssa" in reason:
    classification = "typed-optimizer-or-machine"
elif "symbol" in reason or "link" in reason:
    classification = "symbol-or-linkage"
elif stage == "frontend-diagnostic":
    classification = "frontend-error"
else:
    classification = "unclassified"
print("EDG_C_TYPEID_FAILURE_STAGE=" + stage)
print("EDG_C_TYPEID_REJECTION_CLASS=" + classification)
# Further bounded *non-source-derived* feature flags help distinguish a
# missing adapter capability from a malformed graph, without leaking names.
for key,needles in {
    "UNSUPPORTED":("unsupported","not supported"),
    "UNPROVEN":("not a proven","not proven","cannot prove"),
    "MISSING":("missing","not found"),
    "INCOMPLETE":("incomplete","truncated"),
    "MALFORMED":("malformed","invalid native"),
    "DYNAMIC":("dynamic","startup","init_array"),
}.items():
    print("EDG_C_TYPEID_REASON_" + key + "="
          + ("yes" if any(word in reason for word in needles) else "no"))
PY
# The compiler's opt-in diagnostic records ONLY numeric EDG source facts.
# Validate every key/value in an explicit allowlist before echoing to CI;
# never grep raw source/diagnostic text directly into public Action logs.
python3 - /audit-tmp/edg-generated-c-native.log <<'PY'
from pathlib import Path
import re,sys
fields=("linkage","explicit","tls","local","type_class","init_kind",
        "init_explicit","init_uninitialized","image_kind",
        "layout_bytes","layout_align","field_count","base_count","dynamic",
        "materialized","weak","alias","section","nonreal",
        "compiler_generated","globals","definitions")
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    prefix="EDG_TYPEID_SAFE_FAIL_GLOBAL "
    if not line.startswith(prefix): continue
    entries=line[len(prefix):].split()
    if len(entries)!=len(fields): continue
    values=[]
    for entry,key in zip(entries,fields):
        m=re.fullmatch(re.escape(key)+r"=([0-9]{1,10})",entry)
        if m is None: break
        values.append(m.group(1))
    else:
        print(prefix+" ".join(f"{k}={v}" for k,v in zip(fields,values)))
        break
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
