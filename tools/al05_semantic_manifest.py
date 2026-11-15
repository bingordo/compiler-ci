#!/usr/bin/env python3
"""Content-address the exact AL-05 test-local semantic assumptions and 8 Z3 replays.

This is deliberately NOT the unfinished FS-1 normative IR specification:
a source-controlled human-reviewable contract is checked against Rust's
production Op flags and SMT obligations, then SHA256-bound to CI's source SHA
and both unchanged v1 and affine v2 canonical TypeId replay records.
The manifest is integrity/provenance evidence, NOT authentication or proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validate_al05_replay import SCHEMAS, SOURCE_SHA, validate

FORMAT = "al05-semantic-scope-evidence-1"
SCOPE_LINES = (
    "AL05_SEMANTIC_SCOPE 1",
    "status=test-local-not-normative-FS1",
    "ir=compiler-ir-native-module-3",
    "contracts=al05-closed-i32-cse-loop-v1,al05-closed-i32-affine-cse-loop-v2",
    "width=i32-bitvector-32",
    "add=modular-bvadd",
    "comparison=signed-bvslt",
    "division=signed-bvsdiv-trap-zero-or-intmin-negone",
    "divs-observation=body-only-path-conditional",
    "division-speculation=forbidden",
    "block-args=parallel-edge-assignment",
    "cfg=entry-header-body-latch-exit",
    "loop-counter=zero-initialized-plus-one-signed-bound",
    "result=loop-carried-accumulator",
    "memory=none",
    "external-effects=none",
    "optimization=real-typed-cse",
)
SCOPE_BYTES = ("\n".join(SCOPE_LINES) + "\n").encode("ascii")
MAX_SCOPE_BYTES = 4096
MAX_MANIFEST_BYTES = 8192


def digest(data: bytes) -> str:
    """Stable content digest; does not authenticate the producer."""
    return hashlib.sha256(data).hexdigest()


def read_scope(path: Path) -> bytes:
    """Fail closed on any new, deleted, reordered or reformatted assumption."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SCOPE_BYTES:
        raise ValueError("AL-05 semantic scope missing, redirected or too large")
    raw = path.read_bytes()
    if raw != SCOPE_BYTES:
        raise ValueError("AL-05 reviewed semantic scope changed or unsupported")
    return raw


def build(root: Path, source_sha: str, scope_path: Path) -> dict:
    """Validate both families then derive their exact byte lengths/digests."""
    if SOURCE_SHA.fullmatch(source_sha) is None:
        raise ValueError("AL-05 source must be a complete lowercase commit SHA")
    if validate(root, source_sha) != 8:
        raise ValueError("AL-05 both exact four-seed families are required")
    scope = read_scope(scope_path)
    snapshots = {}
    for family in SCHEMAS:
        for seed in range(4):
            name = f"al05-{family}-{seed}.replay"
            path = root / name
            data = path.read_bytes()
            snapshots[name] = {
                "bytes": len(data),
                "sha256": digest(data),
                "contract": SCHEMAS[family]["contract"],
            }
    return {
        "format": FORMAT,
        "normative_fs1": False,
        "compiler_sha": source_sha,
        "ir": "compiler-ir-native-module-3",
        "scope_sha256": digest(scope),
        "scope_file": "al05_semantic_scope_v1.txt",
        "snapshots": snapshots,
    }


def canonical(document: dict) -> bytes:
    """Canonical, stable, newline-terminated JSON for later comparison."""
    return (json.dumps(document, sort_keys=True, indent=2,
                       ensure_ascii=True) + "\n").encode("ascii")


def verify_manifest(
    manifest: Path, root: Path, source_sha: str, scope_path: Path,
) -> dict:
    """Reject any added claim, stale source, changed file or broken inventory."""
    if manifest.is_symlink() or not manifest.is_file() or \
            manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("AL-05 scope manifest missing or redirected")
    recorded_bytes = manifest.read_bytes()
    try:
        recorded = json.loads(recorded_bytes)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("AL-05 invalid scope JSON") from exc
    if not isinstance(recorded, dict):
        raise ValueError("AL-05 scope manifest is not an object")
    if recorded_bytes != canonical(recorded):
        raise ValueError("AL-05 scope manifest is not canonically encoded")
    actual = build(root, source_sha, scope_path)
    if recorded != actual:
        raise ValueError("AL-05 scope manifest does not bind current source, scope and 8 records")
    return actual


def main() -> None:
    """Write one evidence manifest or independently recheck an archived one."""
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--replay-dir", type=Path, required=True)
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if args.create:
        data = build(args.replay_dir, args.source_sha, args.scope_file)
        args.manifest.write_bytes(canonical(data))
    else:
        data = verify_manifest(args.manifest, args.replay_dir,
                               args.source_sha, args.scope_file)
    assert len(data["snapshots"]) == 8
    print(f"AL05_SCOPE_MANIFEST files=8 source_sha={args.source_sha} "
          f"scope_sha256={data['scope_sha256']} normative_fs1=false verdict=PASS")


if __name__ == "__main__":
    main()
