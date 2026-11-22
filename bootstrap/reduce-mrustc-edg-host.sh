#!/usr/bin/env bash
# C-Reduce on the Ubuntu Actions runner; test every candidate with the exact
# EDG Fedora container, whose native bridge/parser binary was built by the
# preceding generated-C audit. No C-Reduce build or Fedora RPM is required.
# Input, intermediate/output C, raw logs remain in RUNNER_TEMP, never cached.
set -euo pipefail
if (( $# != 1 )); then echo "usage: $0 /absolute/generated/small.c" >&2; exit 2; fi
source_file="$1"
test -s "$source_file"
test -x "$PWD/edgcpp-rust/target/debug/examples/ci_edg_parse_probe"
test -f "$PWD/edgcpp/build/gcc-release/environment.sh"
command -v creduce
command -v docker
command -v python3
test -n "${RUNNER_TEMP:-}"
work="$RUNNER_TEMP/edg-creduce-host"
mkdir -p "$work" "$RUNNER_TEMP/edg-creduce-temporary"
cp "$source_file" "$work/reduced.c"
original_bytes=$(stat -c %s "$work/reduced.c")
case "$source_file" in
  "$PWD"/*) source_container="/workspace/${source_file#"$PWD"/}" ;;
  *) echo "generated source outside workspace" >&2; exit 2 ;;
esac
export EDG_SOURCE_INCLUDE="$(dirname "$source_container")"
export EDG_CREDUCE_DOCKER_CONTAINER="edg-creduce-$GITHUB_RUN_ID"
export EDG_CREDUCE_INCLUDE_LIST="$work/include-flags.txt"
export EDG_CREDUCE_PARSER="/workspace/edgcpp-rust/target/debug/examples/ci_edg_parse_probe"

docker rm -f "$EDG_CREDUCE_DOCKER_CONTAINER" >/dev/null 2>&1 || true
docker run --rm -d --name "$EDG_CREDUCE_DOCKER_CONTAINER" \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  --entrypoint /bin/bash -v "$PWD:/workspace" -v "$RUNNER_TEMP:/audit-tmp" \
  -e EDG_BASE=/workspace/edgcpp/bases/docker/dev-env/gcc \
  -e EDGCPP_SOURCE_DIR=/workspace/edgcpp \
  -e EDGCPP_COMPILE_COMMANDS=/workspace/edgcpp/build/gcc-release/compile_commands.json \
  -e EDGCPP_LIB_DIR=/workspace/edgcpp/build/gcc-release/lib \
  -e EDGCPP_EDG_REVISION=b195677be8b9b56c8337afad6be8064d21a54082 \
  ghcr.io/edgcpp/ci-runner@sha256:14b3948c2ce12cfa5472e5a6c468393d15426f3a67685cc35d830ea5db2acdf8 \
  -c 'sleep 2400' >/dev/null
trap 'docker rm -f "$EDG_CREDUCE_DOCKER_CONTAINER" >/dev/null 2>&1 || true' EXIT
version=$(docker exec "$EDG_CREDUCE_DOCKER_CONTAINER" gcc -dumpfullversion -dumpversion)
export EDG_CREDUCE_GNU="$(python3 - "$version" <<'PY'
import sys
parts=[int(s) for s in sys.argv[1].split(".")]
print(parts[0]*10000 + (parts[1] if len(parts)>1 else 0)*100 + (parts[2] if len(parts)>2 else 0))
PY
)"
docker exec "$EDG_CREDUCE_DOCKER_CONTAINER" bash -c \
  'gcc -E -x c /dev/null -v 2>&1 |
   awk "/#include <...> search starts here:/{active=1;next} /^End of search list/{active=0} active && /^ /{sub(/^[[:space:]]+/, \"\");print}"' \
  > "$work/fedora-includes.txt"
sed -e '/^$/d' -e 's/^/-I/' "$work/fedora-includes.txt" > "$EDG_CREDUCE_INCLUDE_LIST"
echo "-I$EDG_SOURCE_INCLUDE" >> "$EDG_CREDUCE_INCLUDE_LIST"
echo "EDG_CREDUCE_INPUT_BYTES=$original_bytes"
# Check whether moving the TU to C-Reduce's temporary directory changes
# the failure. Keep raw source diagnostics runner-local.
set +e
docker exec -w /workspace/edgcpp/build/gcc-release "$EDG_CREDUCE_DOCKER_CONTAINER" \
  bash -c '
    ulimit -c 0
    source ./environment.sh
    timeout --signal=TERM --kill-after=3s 15s "$1" \
      --gcc --c11 --no_strict_gnu --gnu_version "$2" "$3"
  ' bash "$EDG_CREDUCE_PARSER" "$EDG_CREDUCE_GNU" "$source_container" \
  > "$work/original-direct.log" 2>&1
original_rc=$?
set -e
echo "EDG_CREDUCE_ORIGINAL_DIRECT_RC=$original_rc"
echo "EDG_CREDUCE_TOOL_VERSION=$(creduce --version | head -n 1 | tr -dc 'A-Za-z0-9. -' | cut -c1-64)"

cat > "$work/interesting.sh" <<'TEST'
#!/usr/bin/env bash
# C-Reduce runs this script with its candidate named reduced.c in the CWD.
set -uo pipefail
candidate="$PWD/reduced.c"
test -s "$candidate" || exit 1
case "$candidate" in "$RUNNER_TEMP"/*) : ;; *) exit 1 ;; esac
container_candidate="/audit-tmp/${candidate#"$RUNNER_TEMP"/}"
args=()
while IFS= read -r line; do
  [[ -n "$line" ]] && args+=("$line")
done < "$EDG_CREDUCE_INCLUDE_LIST"
# Direct parser gate: reject source errors cheaply before spawning GDB.
set +e
docker exec -w /workspace/edgcpp/build/gcc-release "$EDG_CREDUCE_DOCKER_CONTAINER" \
  bash -c '
    ulimit -c 0
    source ./environment.sh
    timeout --signal=TERM --kill-after=3s 15s \
      "$1" --gcc --c11 --no_strict_gnu --gnu_version "$2" "${@:4}" "$3"
  ' bash "$EDG_CREDUCE_PARSER" "$EDG_CREDUCE_GNU" "$container_candidate" "${args[@]}" \
  > "$PWD/interesting-direct.log" 2>&1
direct_rc=$?
printf '%s\n' "$direct_rc" > "$PWD/interesting-direct.rc"
set -e
test "$direct_rc" -eq 139 || exit 1
# Same exact SIGSEGV top-frame proof, not a different crash.
docker exec -w /workspace/edgcpp/build/gcc-release "$EDG_CREDUCE_DOCKER_CONTAINER" \
  bash -c '
    ulimit -c 0
    source ./environment.sh
    timeout --signal=TERM --kill-after=3s 20s \
      gdb -nx --batch --quiet \
        -ex "set pagination off" \
        -ex "set debuginfod enabled off" \
        -ex "set print frame-arguments none" \
        -ex run -ex "bt 12" --args "$1" \
        --gcc --c11 --no_strict_gnu --gnu_version "$2" "${@:4}" "$3"
  ' bash "$EDG_CREDUCE_PARSER" "$EDG_CREDUCE_GNU" "$container_candidate" "${args[@]}" \
  > "$PWD/interesting-gdb.log" 2>&1 || exit 1
grep -Fq 'Program received signal SIGSEGV' "$PWD/interesting-gdb.log" &&
grep -Eq '^#0[[:space:]]+.*edg::plain_cpp_record' "$PWD/interesting-gdb.log"
TEST
chmod 700 "$work/interesting.sh"

if ! (cd "$work" && ./interesting.sh); then
  echo "EDG_CREDUCE_STATUS=baseline_not_same_crash"
  if [[ -s "$work/interesting-direct.rc" ]]; then
    echo "EDG_CREDUCE_RELOCATED_DIRECT_RC=$(cat "$work/interesting-direct.rc")"
  else
    echo "EDG_CREDUCE_RELOCATED_DIRECT_RC=missing"
  fi
  if [[ -s "$work/interesting-gdb.log" ]]; then
    grep -Fq 'Program received signal SIGSEGV' "$work/interesting-gdb.log" &&
      echo "EDG_CREDUCE_RELOCATED_GDB_SIGSEGV=yes" ||
      echo "EDG_CREDUCE_RELOCATED_GDB_SIGSEGV=no"
    # No path, arguments, or source excerpts in published summary.
    if grep -Eq '^#0[[:space:]]+.*edg::plain_cpp_record' "$work/interesting-gdb.log"; then
      echo "EDG_CREDUCE_RELOCATED_GDB_TOP=plain_cpp_record"
    else
      echo "EDG_CREDUCE_RELOCATED_GDB_TOP=other"
    fi
  fi
  exit 0
fi
echo "EDG_CREDUCE_BASELINE=same_plain_cpp_record_sigsegv"
set +e
(
  cd "$work"
  export TMPDIR="$RUNNER_TEMP/edg-creduce-temporary"
  # Two workers and no output/cache of generated input. C-Reduce cannot
  # exceed the requested total wall-clock budget.
  timeout --signal=TERM --kill-after=10s 12m \
    creduce --n 2 --timeout 30 ./interesting.sh reduced.c
) > "$work/creduce.log" 2>&1
reduce_rc=$?
set -e
if (cd "$work" && ./interesting.sh); then
  bytes=$(stat -c %s "$work/reduced.c")
  echo "EDG_CREDUCE_STATUS=confirmed_same_crash"
  echo "EDG_CREDUCE_EXIT=$reduce_rc"
  echo "EDG_CREDUCE_OUTPUT_BYTES=$bytes"
  echo "EDG_CREDUCE_SHA256=$(sha256sum "$work/reduced.c" | cut -d' ' -f1)"
  if (( bytes <= 8192 )); then
    # User explicitly requested a small issue reproducer. A bounded source
    # preview permits human review before posting to public GitHub issue.
    echo "EDG_CREDUCE_TESTCASE_BEGIN"
    cat "$work/reduced.c"
    echo "EDG_CREDUCE_TESTCASE_END"
  else
    echo "EDG_CREDUCE_TESTCASE_STATUS=too_large_for_issue"
  fi
else
  echo "EDG_CREDUCE_STATUS=final_candidate_not_interesting"
  echo "EDG_CREDUCE_EXIT=$reduce_rc"
fi
