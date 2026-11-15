//! Compare native rustc and mrustc-generated GCC executables on real optimizer IR.
//! The oracle exercises deterministic wrapping arithmetic and SSA verification.

use compiler_ir::{Block, BlockId, Function, Instruction, Op, Terminator, Type, ValueId};
use compiler_opt::fold_constants;

fn main() {
    let mut checksum = 0u64;
    for i in 0..128i32 {
        let lhs = if i % 2 == 0 { i32::MAX - i } else { i.wrapping_mul(7717) };
        let rhs = if i % 3 == 0 { i32::MIN + i } else { -i.wrapping_mul(313) };
        for variant in 0..3 {
            let (operation, expected) = match variant {
                0 => (Op::Add(ValueId(0), ValueId(1)), lhs.wrapping_add(rhs)),
                1 => (Op::Sub(ValueId(0), ValueId(1)), lhs.wrapping_sub(rhs)),
                _ => (Op::Mul(ValueId(0), ValueId(1)), lhs.wrapping_mul(rhs)),
            };
            let mut function = Function {
                name: "arithmetic_oracle".into(),
                blocks: vec![Block {
                    id: BlockId(0),
                    params: vec![],
                    instructions: vec![
                        Instruction { id: ValueId(0), ty: Type::I32, op: Op::ConstI32(lhs) },
                        Instruction { id: ValueId(1), ty: Type::I32, op: Op::ConstI32(rhs) },
                        Instruction { id: ValueId(2), ty: Type::I32, op: operation },
                    ],
                    end: Terminator::Return(ValueId(2)),
                }],
            };
            compiler_ir::verify(&function).expect("valid initial SSA");
            assert_eq!(fold_constants(&mut function).expect("fold_constants"), 1);
            compiler_ir::verify(&function).expect("valid optimized SSA");
            match &function.blocks[0].instructions[2].op {
                Op::ConstI32(actual) => assert_eq!(*actual, expected),
                _ => panic!("the arithmetic instruction was not folded"),
            }
            checksum = checksum.wrapping_mul(16777619).wrapping_add(expected as u32 as u64);
        }
    }
    println!("compiler-opt-smoke:{}", checksum);
}
