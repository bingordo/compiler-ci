#!/usr/bin/env bash
# Minimize a real mrustc-generated C input preserving the exact EDG bridge
# SIGSEGV (plain_cpp_record). Invoked inside the pinned EDG audit container.
# Never publish generated C, GDB logs, or C-Reduce output from public CI.
set -euo pipefail
if (( $# < 3 )); then
  echo "usage: reduce-mrustc-edg-crash.sh parser gnu_version source [EDG include flags...]" >&2
  exit 2
fi
parser="$1"
gnu_version="$2"
source="$3"
shift 3
test -x "$parser"
test -s "$source"
test -d /audit-tmp
if ! command -v gdb >/dev/null 2>&1; then
  echo "EDG_CREDUCE_STATUS=skipped_gdb_unavailable"
  exit 0
fi
# The outer EDG audit installs distribution gdb/creduce packages once per job,
# with the downloaded .deb binaries restored by actions/cache. Never run apt
# inside the thousands of C-Reduce interestingness test invocations.
if ! command -v creduce >/dev/null 2>&1; then
  echo "EDG_CREDUCE_STATUS=skipped_creduce_unavailable"
  exit 0
fi

work=/audit-tmp/edg-creduce-small
mkdir -p "$work"
cp "$source" "$work/reduced.c"
# Source file location matters for quote includes when C-Reduce copies the
# candidate into temporary directories. Keep original include roots available.
printf '%s\n' "$@" "-I$(dirname "$source")" > "$work/include-flags.txt"
export EDG_CREDUCE_PARSER="$parser"
export EDG_CREDUCE_GNU="$gnu_version"
export EDG_CREDUCE_INCLUDES="$work/include-flags.txt"
# Also supply include directories to C-Reduce's clang_delta passes.
export CREDUCE_INCLUDE_PATH="$(dirname "$source"):${CREDUCE_INCLUDE_PATH:-}"

# C-Reduce runs this test in a fresh directory with its current reduced.c.
# Require BOTH SIGSEGV and the same GDB top frame; a different parser crash,
# timeout, source rejection, or failed GDB invocation is not interesting.
cat > "$work/interesting.sh" <<'TEST'
#!/usr/bin/env bash
set -uo pipefail
candidate="$PWD/reduced.c"
test -s "$candidate" || exit 1
set --
while IFS= read -r flag; do
  test -n "$flag" && set -- "$@" "$flag"
done < "$EDG_CREDUCE_INCLUDES"
# Most reductions produce normal parse errors. Reject those with a direct,
# cheap parser invocation; only start GDB when the process actually SIGSEGVs.
# Preserve working directory, dialect flags and input path for both runs.
(
  cd /workspace/edgcpp/build/gcc-release || exit 1
  ulimit -c 0
  timeout --signal=TERM --kill-after=3s 15s \
    "$EDG_CREDUCE_PARSER" --gcc --c11 --no_strict_gnu \
      --gnu_version "$EDG_CREDUCE_GNU" "$@" "$candidate"
) > "$PWD/interesting-direct.log" 2>&1
direct_rc=$?
test "$direct_rc" -eq 139 || exit 1
(
  cd /workspace/edgcpp/build/gcc-release || exit 1
  ulimit -c 0
  timeout --signal=TERM --kill-after=3s 20s \
    gdb -nx --batch --quiet \
      -ex 'set pagination off' \
      -ex 'set confirm off' \
      -ex 'set debuginfod enabled off' \
      -ex 'set print frame-arguments none' \
      -ex 'run' \
      -ex 'bt 12' \
      --args "$EDG_CREDUCE_PARSER" --gcc --c11 --no_strict_gnu \
        --gnu_version "$EDG_CREDUCE_GNU" "$@" "$candidate"
) > "$PWD/interesting-gdb.log" 2>&1 || exit 1
grep -Fq 'Program received signal SIGSEGV' "$PWD/interesting-gdb.log" || exit 1
grep -Eq '^#0[[:space:]]+.*edg::plain_cpp_record' "$PWD/interesting-gdb.log"
TEST
chmod 700 "$work/interesting.sh"

original_bytes=$(stat -c %s "$work/reduced.c")
echo "EDG_CREDUCE_INPUT_BYTES=$original_bytes"
if ! (cd "$work" && ./interesting.sh); then
  echo "EDG_CREDUCE_STATUS=baseline_not_same_crash"
  exit 0
fi
echo "EDG_CREDUCE_BASELINE=same_plain_cpp_record_sigsegv"

set +e
(
  cd "$work"
  # C-Reduce itself is bounded; its own stdout, iterations and intermediate
  # source remain runner-local. Two workers keep CI memory pressure bounded.
  timeout --signal=TERM --kill-after=10s 12m \
    creduce --n 2 --timeout 30 "$work/interesting.sh" reduced.c
) > "$work/creduce.log" 2>&1
reduce_rc=$?
set -e

if (cd "$work" && ./interesting.sh); then
  result_bytes=$(stat -c %s "$work/reduced.c")
  reduction_pct=$(python3 - "$original_bytes" "$result_bytes" <<'PY'
import sys
original, reduced = map(int, sys.argv[1:])
print(f"{100*(original-reduced)/original:.1f}" if original else "0.0")
PY
)
  result_sha=$(sha256sum "$work/reduced.c" | cut -d' ' -f1)
  echo "EDG_CREDUCE_STATUS=confirmed_same_crash"
  echo "EDG_CREDUCE_EXIT=$reduce_rc"
  echo "EDG_CREDUCE_OUTPUT_BYTES=$result_bytes"
  echo "EDG_CREDUCE_REDUCTION_PERCENT=$reduction_pct"
  echo "EDG_CREDUCE_SHA256=$result_sha"
  echo "EDG_CREDUCE_SOURCE=runner_local_only"
else
  echo "EDG_CREDUCE_STATUS=final_candidate_not_interesting"
  echo "EDG_CREDUCE_EXIT=$reduce_rc"
fi
