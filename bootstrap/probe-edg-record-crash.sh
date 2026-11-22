#!/usr/bin/env bash
# Public C record candidates: report only a GDB-confirmed plain_cpp_record crash.
set -euo pipefail
cd /workspace
export PATH="$HOME/.cargo/bin:$PATH"
export EDG_BASE=/workspace/edgcpp/bases/docker/dev-env/gcc
export EDGCPP_SOURCE_DIR=/workspace/edgcpp
export EDGCPP_COMPILE_COMMANDS=/workspace/edgcpp/build/gcc-release/compile_commands.json
export EDGCPP_LIB_DIR=/workspace/edgcpp/build/gcc-release/lib
export EDGCPP_EDG_REVISION=b195677be8b9b56c8337afad6be8064d21a54082
for checkout in /workspace/edgcpp /workspace/edgcpp-rust; do
  git config --global --add safe.directory "$checkout"
done
test "$(git -C edgcpp rev-parse HEAD)" = "$EDGCPP_EDG_REVISION"
test "$(git -C edgcpp-rust rev-parse HEAD)" = fc5adba5dc9061cc24517544c23ee92ab0151b5e
if ! command -v cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
    sh -s -- -y --profile minimal --default-toolchain 1.99.1
fi
rustup toolchain install 1.99.1 --profile minimal
rustup default 1.99.1
(cd edgcpp && cmake --preset linux-gcc-release -DEDG_BUILD_LIB=OFF -DEDG_BUILD_CALLABLE_LIBRARY=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON && cmake --build build/gcc-release --target error-info --parallel 2)
mkdir -p edgcpp/build/gcc-release/lib edgcpp/build/prebuilt-edg
curl --fail --location --silent --show-error --retry 4 --retry-delay 2 \
  "https://github.com/bingundu/edgcpp/releases/download/edgcpfe-v2026.11.14-upstream-177765bb7731/edgcpfe-ci-upstream-callable-edg-release-20261114-linux-x86_64.tar.gz" \
  -o edgcpp/build/prebuilt-edg/archive.tar.gz
printf '%s  %s\n' \
  1ce495b629924f31382ecccc891a7a72caae1014ddfa2ba7dd39b44d8b0a9526 \
  edgcpp/build/prebuilt-edg/archive.tar.gz | sha256sum -c -
tar -xzf edgcpp/build/prebuilt-edg/archive.tar.gz -C edgcpp/build/prebuilt-edg
cp edgcpp/build/prebuilt-edg/lib/libedgcpfe.a edgcpp/build/gcc-release/lib/libedgcpfe.a
test ! -e edgcpp-rust/examples/ci_edg_parse_probe.rs
cp bootstrap/edg_parse_probe.rs edgcpp-rust/examples/ci_edg_parse_probe.rs
cargo build --quiet --manifest-path edgcpp-rust/Cargo.toml --example ci_edg_parse_probe
parser=/workspace/edgcpp-rust/target/debug/examples/ci_edg_parse_probe
source edgcpp/build/gcc-release/environment.sh
version=$(gcc -dumpfullversion -dumpversion)
IFS=. read -r major minor patch <<< "$version"
gnu_number=$((10#${major}*10000+10#${minor:-0}*100+10#${patch:-0}))
mkdir -p /audit-tmp/record-slices
ulimit -c 0

cat > /audit-tmp/record-slices/01_global_tagged.c <<'C'
struct Node { int value; };
struct Node g;
int main(void) { return g.value; }
C
cat > /audit-tmp/record-slices/02_recursive.c <<'C'
struct Node { struct Node *next; int value; };
struct Node g;
int main(void) { return g.next != 0; }
C
cat > /audit-tmp/record-slices/03_typedef.c <<'C'
typedef struct Node { int value; } Node;
Node g;
int main(void) { return g.value; }
C
cat > /audit-tmp/record-slices/04_union_member.c <<'C'
struct Node { union { int x; long y; }; int z; };
struct Node g;
int main(void) { return g.z; }
C
cat > /audit-tmp/record-slices/05_union.c <<'C'
union U { int x; long y; };
union U g;
int main(void) { return g.x; }
C
cat > /audit-tmp/record-slices/06_function_pointer.c <<'C'
struct Node { int (*call)(int); struct Node *next; };
struct Node g;
int main(void) { return g.call != 0; }
C
cat > /audit-tmp/record-slices/07_anonymous_typedef.c <<'C'
typedef struct { int x; } Anon;
Anon g;
int main(void) { return g.x; }
C
cat > /audit-tmp/record-slices/08_nested.c <<'C'
struct Inner { int x; };
struct Outer { struct Inner value; };
struct Outer g;
int main(void) { return g.value.x; }
C
cat > /audit-tmp/record-slices/09_wide_int.c <<'C'
struct Wide { unsigned __int128 value; };
struct Wide g;
int main(void) { return (int)g.value; }
C
cat > /audit-tmp/record-slices/10_attribute.c <<'C'
struct __attribute__((aligned(32))) Node { int value; };
struct Node g;
int main(void) { return g.value; }
C
cat > /audit-tmp/record-slices/11_mutual_recursive.c <<'C'
struct A;
struct B { struct A *a; };
struct A { struct B *b; };
struct A g;
int main(void) { return g.b != 0; }
C
cat > /audit-tmp/record-slices/12_nested_array.c <<'C'
struct Node { int data[32]; struct Node *next; };
struct Node g[2];
int main(void) { return g[1].data[3]; }
C


# EDG GCC-mode __builtin_va_list is an intrinsic array/record type.
# Its class tag/supplement may differ from an ordinary source C struct.
cat > /audit-tmp/record-slices/13_builtin_va_global.c <<'C'
typedef __builtin_va_list Va;
Va g;
int main(void) { return 0; }
C
cat > /audit-tmp/record-slices/14_builtin_va_record.c <<'C'
struct Node { __builtin_va_list ap; };
struct Node g;
int main(void) { return 0; }
C
cat > /audit-tmp/record-slices/15_builtin_va_local.c <<'C'
int f(int last, ...) {
  __builtin_va_list ap;
  __builtin_va_start(ap, last);
  __builtin_va_end(ap);
  return last;
}
C
cat > /audit-tmp/record-slices/16_va_ptr.c <<'C'
typedef __builtin_va_list Va;
Va *g;
int main(void) { return 0; }
C
cat > /audit-tmp/record-slices/17_builtin_va_nested.c <<'C'
struct Node { __builtin_va_list ap; struct Node *next; };
struct Node g;
int main(void) { return 0; }
C
cat > /audit-tmp/record-slices/18_builtin_va_qualifier.c <<'C'
typedef __builtin_va_list Va;
extern volatile Va g;
int f(void) { return sizeof(g); }
C

for input in /audit-tmp/record-slices/*.c; do
  name=$(basename "$input" .c)
  gcc -std=gnu11 -fsyntax-only "$input"
  set +e
  (
    cd /workspace/edgcpp/build/gcc-release
    timeout --signal=TERM --kill-after=2s 20s \
      "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$input"
  ) > "/audit-tmp/record-slices/$name.log" 2>&1
  rc=$?
  set -e
  echo "EDG_RECORD_CASE_${name}_EXIT=$rc"
  if (( rc == 139 )); then
    set +e
    (
      cd /workspace/edgcpp/build/gcc-release
      timeout --signal=TERM --kill-after=3s 30s \
        gdb -nx --batch --quiet \
          -ex 'set pagination off' -ex 'set print frame-arguments none' \
          -ex 'set debuginfod enabled off' -ex run -ex 'bt 10' \
          --args "$parser" --gcc --c11 --no_strict_gnu --gnu_version "$gnu_number" "$input"
    ) > "/audit-tmp/record-slices/$name.gdb.log" 2>&1
    set -e
    if grep -Fq 'Program received signal SIGSEGV' "/audit-tmp/record-slices/$name.gdb.log" &&
       grep -Eq '^#0[[:space:]]+.*edg::plain_cpp_record' "/audit-tmp/record-slices/$name.gdb.log"; then
      echo "EDG_RECORD_SAME_FRAME=$name"
      echo "EDG_RECORD_SOURCE_BEGIN"
      cat "$input"
      echo "EDG_RECORD_SOURCE_END"
      exit 0
    fi
  fi
done
echo "EDG_RECORD_SAME_FRAME=none_found"
