#!/usr/bin/env python3
"""Insert identical temporary RV64 register-allocation observers into A/B builds.

Only the compiler-ci checkout is modified. The production compiler history
is untouched, and a run-time environment guard keeps ordinary invocations
free of instrumentation. Both revisions must have identical emitter source.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import sys

RELATIVE = Path("crates/backend-riscv64/src/leaf.rs")
ANCHOR = """    let frame = Frame::new(&allocation, has_call, ingress_slots,
                           outgoing_slots, edge_slots, call_slots)?;
    let mut out = Vec::new();"""
REPLACEMENT = """    let frame = Frame::new(&allocation, has_call, ingress_slots,
                           outgoing_slots, edge_slots, call_slots)?;
    // Temporary, benchmark-only observer. The shared allocator owns all
    // spill homes; count them directly rather than guessing from stack ops.
    if std::env::var_os("RV64_CI_ALLOC_STATS").is_some() {
        let spill_values = allocation.homes.values()
            .filter(|home| matches!(home, Home::Stack(_))).count();
        eprintln!(
            "RV64_ALLOC_STATS name={} spill_slots={} spill_values={} callee_saved={} frame_bytes={} virtual_homes={} blocks={}",
            function.name, allocation.spill_slots, spill_values,
            frame.saved_count, frame.size, allocation.homes.len(),
            function.blocks.len(),
        );
    }
    let mut out = Vec::new();"""

def main() -> None:
    roots = [Path(s) for s in sys.argv[1:]]
    assert len(roots) == 2, "need baseline and candidate checkouts"
    sources = [(root / RELATIVE).read_text() for root in roots]
    assert sources[0] == sources[1], "physical RV64 emitters differ before instrumentation"
    assert sources[0].count(ANCHOR) == 1, "allocator instrumentation point changed"
    assert "RV64_ALLOC_STATS" not in sources[0], "instrumentation already present"
    patched = sources[0].replace(ANCHOR, REPLACEMENT)
    digest = hashlib.sha256(patched.encode()).hexdigest()
    for root in roots:
        (root / RELATIVE).write_text(patched)
        print(f"allocator observer {root}: sha256={digest}")

if __name__ == "__main__":
    main()
