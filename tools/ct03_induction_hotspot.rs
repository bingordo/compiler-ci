//! CT-03 isolated exact-parent benchmark of actually transformed counted
//! induction loops. Both versions use the same copied, verifier-valid input.
use compiler_ir::{verify, Block, BlockId, Cmp, Function, Instruction, Op, Parameter, Target, Terminator, Type, ValueId};
use std::{hash::{Hash, Hasher}, hint::black_box, time::Instant};

fn v(x: u32) -> ValueId { ValueId(x) }
fn i(x: u32, op: Op) -> Instruction {
    Instruction { id: v(x), ty: op.result_type(), op }
}
fn p(x: u32) -> Parameter { Parameter { id: v(x), ty: Type::I32 } }
fn to(block: u32, args: &[u32]) -> Target {
    Target { block: BlockId(block), args: args.iter().copied().map(v).collect() }
}
fn fixture(products: u32) -> Function {
    assert!((1..=16).contains(&products));
    let mut latch = Vec::new();
    let mut sum = 6;
    for j in 0..products {
        let mul = 1000 + j;
        let next_sum = 2000 + j;
        latch.push(i(mul, Op::Mul(v(5), v(4))));
        latch.push(i(next_sum, Op::Add(v(sum), v(mul))));
        sum = next_sum;
    }
    latch.push(i(10, Op::Add(v(5), v(2))));
    let f = Function {
        name: format!("ct03_induction_{products}"),
        blocks: vec![
            Block {
                id: BlockId(0), params: vec![p(0)],
                instructions: vec![
                    i(1, Op::ConstI32(0)),
                    i(2, Op::ConstI32(3)),
                    i(3, Op::ConstI32(13)),
                    i(4, Op::ConstI32(29)),
                ],
                end: Terminator::Jump(to(1, &[1, 0])),
            },
            Block {
                id: BlockId(1), params: vec![p(5), p(6)],
                instructions: vec![
                    i(7, Op::Cmp(Cmp::Lt, v(5), v(3))),
                ],
                end: Terminator::Branch {
                    condition: v(7), yes: to(2, &[]), no: to(3, &[]),
                },
            },
            Block {
                id: BlockId(2), params: vec![],
                instructions: latch,
                end: Terminator::Jump(to(1, &[10, sum])),
            },
            Block {
                id: BlockId(3), params: vec![],
                instructions: vec![],
                end: Terminator::Return(v(6)),
            },
        ],
    };
    verify(&f).unwrap();
    f
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 4, "ct03_induction_hotspot <case> <products> <iterations>");
    let case = args[1].as_str();
    assert_eq!(case, "induction_products");
    let size: u32 = args[2].parse().unwrap();
    let iterations: usize = args[3].parse().unwrap();
    assert!((1..=16).contains(&size) && (1..=200).contains(&iterations));
    let source = fixture(size);
    let mut expected = source.clone();
    let removed = compiler_opt::induction::reduce_multiplications(&mut expected).unwrap();
    assert_eq!(removed, size as usize, "all counted induction multiplications must be eliminated");
    verify(&expected).unwrap();

    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    format!("{expected:?}|{removed}").hash(&mut hasher);
    let signature = hasher.finish();
    let start = Instant::now();
    for _ in 0..iterations {
        let mut work = black_box(source.clone());
        let result = compiler_opt::induction::reduce_multiplications(&mut work).unwrap();
        assert_eq!(result, removed);
        black_box(&work);
    }
    let elapsed = start.elapsed().as_nanos();
    println!(
        "CT04 case={case} size={size} iterations={iterations} ns={elapsed} signature={signature:016x}"
    );
}
