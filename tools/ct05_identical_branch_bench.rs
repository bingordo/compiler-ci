//! CT-05 identical-successor branch batching: exact real optimizer path.
//! Copied unchanged to pinned before/after compiler-opt examples by CI.
use compiler_ir::{
    verify, Block, BlockId, Function, Instruction, Op, Parameter, Target, Terminator, Type, ValueId,
};
use std::{hash::{Hash, Hasher}, hint::black_box, time::Instant};

fn p(n: u32, ty: Type) -> Parameter {
    Parameter { id: ValueId(n), ty }
}
fn t(block: u32, args: &[u32]) -> Target {
    Target { block: BlockId(block), args: args.iter().copied().map(ValueId).collect() }
}
fn dense_equal_successors(n: u32) -> Function {
    assert!(n >= 4);
    let mut blocks = vec![Block {
        id: BlockId(0),
        params: vec![p(0, Type::I32), p(1, Type::Bool)],
        instructions: vec![Instruction {
            id: ValueId(2), ty: Type::I32, op: Op::ConstI32(13),
        }],
        end: Terminator::Branch {
            condition: ValueId(1),
            yes: t(1, &[0]), no: t(1, &[0]),
        },
    }];
    for i in 1..=n {
        let param = 1000 + i;
        blocks.push(Block {
            id: BlockId(i),
            params: vec![p(param, Type::I32)],
            instructions: vec![],
            end: if i == n {
                Terminator::Return(ValueId(param))
            } else {
                Terminator::Branch {
                    condition: ValueId(1),
                    yes: t(i + 1, &[param]),
                    no: t(i + 1, &[if i == n / 2 { 2 } else { param }]),
                }
            },
        });
    }
    Function { name: format!("ct05_identical_{n}"), blocks }
}
fn main() {
    let argv: Vec<String> = std::env::args().collect();
    assert_eq!(argv.len(), 3, "usage: ct05_identical_bench <blocks> <iterations>");
    let size: u32 = argv[1].parse().unwrap();
    let iterations: usize = argv[2].parse().unwrap();
    assert!((4..=512).contains(&size) && (1..=200).contains(&iterations));
    let input = dense_equal_successors(size);
    verify(&input).expect("benchmark must begin with valid typed SSA");
    let mut warmup = input.clone();
    let changed = compiler_opt::if_conversion::fold_identical_merges(&mut warmup)
        .expect("identical successor folding must succeed");
    assert_eq!(changed, (size - 1) as usize);
    verify(&warmup).expect("batch output must verify");
    assert_eq!(compiler_opt::if_conversion::fold_identical_merges(&mut warmup).unwrap(), 0);
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    format!("{warmup:?}|changed={changed}").hash(&mut hasher);
    let signature = hasher.finish();

    let start = Instant::now();
    for _ in 0..iterations {
        let mut f = black_box(input.clone());
        let result = compiler_opt::if_conversion::fold_identical_merges(&mut f)
            .expect("valid fold failed during measurement");
        black_box((&f, result));
        assert_eq!(result, changed);
    }
    let elapsed_ns = start.elapsed().as_nanos();
    println!(
        "CT05 size={size} iterations={iterations} ns={elapsed_ns} signature={signature:016x}"
    );
}
