//! Source-pinned rustc versus mrustc/GCC executable oracle for the
//! *production TypeId SSA* local constant-folding pass.
//! No retired Function pass or legacy optimizer feature is enabled.

use compiler_ir::linkage::ModuleLinkage;
use compiler_ir::typed_cfg_builder::TypedCfgBuilder;
use compiler_ir::typed_module::DefinedSignature;
use compiler_ir::typed_ssa::{ResultType, TypedSsaModule};
use compiler_ir::types::{
    CallingConvention, DataLayout, Endianness, FunctionSignature, PointerLayout, TypeKind, TypeTable,
};
use compiler_ir::{Op, Terminator};
use compiler_opt::typed_local::fold_constants_typed;

fn checked_arithmetic(operation: u8, lhs: i32, rhs: i32, expected: i32) {
    let mut types = TypeTable::new();
    let i32_ty = types.intern(TypeKind::Integer(32)).expect("I32 TypeId");
    let mut cfg = TypedCfgBuilder::new();
    let (entry, _) = cfg.block(&[]);
    let a = cfg.emit(entry, Op::ConstI32(lhs), ResultType::Value(i32_ty));
    let b = cfg.emit(entry, Op::ConstI32(rhs), ResultType::Value(i32_ty));
    let op = match operation {
        0 => Op::Add(a, b),
        1 => Op::Sub(a, b),
        2 => Op::Mul(a, b),
        _ => unreachable!("three arithmetic variants"),
    };
    let result = cfg.emit(entry, op, ResultType::Value(i32_ty));
    cfg.finish_block(entry, Terminator::Return(result))
        .expect("terminated entry block");
    let name = "arithmetic_oracle";
    let mut module = TypedSsaModule {
        functions: vec![cfg.finish(name.to_owned()).expect("native typed SSA")],
        linkage: ModuleLinkage::all_external_names([name]).expect("linkage"),
        globals: vec![],
        types,
        layout: DataLayout::new(Endianness::Little)
            .with_pointer(0, PointerLayout { bits: 64, abi_alignment: 8 })
            .expect("SysV Ptr64"),
        definitions: vec![DefinedSignature {
            symbol: name.into(),
            signature: FunctionSignature {
                params: vec![],
                result: Some(i32_ty),
                variadic: false,
                calling_convention: CallingConvention::C,
            },
        }],
        externals: vec![],
    };
    module.validate().expect("full initial TypeId source/ABI");
    assert_eq!(fold_constants_typed(&mut module).expect("native typed fold"), 1);
    module.validate().expect("full folded TypeId source/ABI");
    let block = &module.functions[0].blocks[0];
    assert_eq!(block.instructions.len(), 3);
    match block.instructions[2].op {
        Op::ConstI32(actual) => assert_eq!(actual, expected),
        _ => panic!("production typed arithmetic instruction was not folded"),
    }
}

fn main() {
    let mut checksum = 0u64;
    for i in 0..128i32 {
        let lhs = if i % 2 == 0 { i32::MAX - i } else { i.wrapping_mul(7717) };
        let rhs = if i % 3 == 0 { i32::MIN + i } else { -i.wrapping_mul(313) };
        for variant in 0..3 {
            let expected = match variant {
                0 => lhs.wrapping_add(rhs),
                1 => lhs.wrapping_sub(rhs),
                _ => lhs.wrapping_mul(rhs),
            };
            checked_arithmetic(variant, lhs, rhs, expected);
            checksum = checksum.wrapping_mul(16777619).wrapping_add(expected as u32 as u64);
        }
    }
    println!("compiler-opt-smoke:{}", checksum);
}
