//! Executable oracle for Rust-to-C bootstrap of compiler-ir.
//! It checks that mrustc's GCC-linked program matches stock rustc on
//! representative public bitvector semantics; this is not a full test suite.

use compiler_ir::native_int_semantics::{checked_const, checked_mask, eval_cast};
use compiler_ir::IntWidthCastKind;

fn main() {
    let mut checksum: u128 = 0;
    for raw in 0..=255u128 {
        let z = eval_cast(IntWidthCastKind::ZeroExtend, 8, 64, raw).expect("zero cast");
        let s = eval_cast(IntWidthCastKind::SignExtend, 8, 64, raw).expect("sign cast");
        let trunc = eval_cast(IntWidthCastKind::Truncate, 64, 8, s).expect("truncation");
        assert_eq!(trunc, raw);
        assert_eq!(z, raw);
        checksum = checksum.wrapping_add(s ^ z);
    }
    assert_eq!(checked_mask(32).unwrap(), 0xffff_ffff);
    assert_eq!(checked_const(8, 256).is_err(), true);
    println!("compiler-ir-smoke:{}", checksum);
}
