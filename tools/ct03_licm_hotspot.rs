//! Identical synthetic verified SSA looping inputs for exact-parent LICM
//! private-transaction clone overhead. This is NOT source-to-ELF timing.
use compiler_ir::{verify, Block, BlockId, Cmp, Function, Instruction, Op, Parameter, Target, Terminator, Type, ValueId};
use std::{hash::{Hash, Hasher}, hint::black_box, time::Instant};

fn v(id: u32) -> ValueId { ValueId(id) }
fn ins(id: u32, op: Op) -> Instruction {
    Instruction { id: v(id), ty: op.result_type(), op }
}
fn to(block: u32, args: &[u32]) -> Target {
    Target { block: BlockId(block), args: args.iter().copied().map(v).collect() }
}
fn fixture(n: u32) -> Function {
    let mut instructions = vec![
        ins(4, Op::ConstI32(1)),
        ins(5, Op::Add(v(3), v(4))),
        ins(6, Op::Cmp(Cmp::Lt, v(5), v(2))),
    ];
    for j in 0..n {
        // Every addition is total and loop invariant, and each has a
        // distinct SSA definition. Only one preheader creation is needed.
        instructions.push(ins(100 + j, Op::Add(v(0), v(2))));
    }
    let f = Function {
        name: "ct03_licm_abba".into(),
        blocks: vec![
            Block {
                id: BlockId(0),
                params: vec![
                    Parameter { id: v(0), ty: Type::I32 },
                    Parameter { id: v(1), ty: Type::Bool },
                ],
                instructions: vec![ins(2, Op::ConstI32(5))],
                end: Terminator::Branch {
                    condition: v(1), yes: to(1, &[]), no: to(2, &[]),
                },
            },
            Block { id: BlockId(1), params: vec![], instructions: vec![],
                    end: Terminator::Jump(to(3, &[0])) },
            Block { id: BlockId(2), params: vec![], instructions: vec![],
                    end: Terminator::Jump(to(3, &[2])) },
            Block {
                id: BlockId(3), params: vec![Parameter { id: v(3), ty: Type::I32 }],
                instructions,
                end: Terminator::Branch {
                    condition: v(6), yes: to(3, &[5]), no: to(4, &[5]),
                },
            },
            Block {
                id: BlockId(4), params: vec![Parameter { id: v(7), ty: Type::I32 }],
                instructions: vec![], end: Terminator::Return(v(7)),
            },
        ],
    };
    verify(&f).expect("valid loop-heavy LICM SSA");
    f
}
fn execute(case: &str, f: &mut Function) -> (usize, usize) {
    match case {
        "licm_preheader" => (compiler_opt::licm::canonicalize_preheaders(f).unwrap(), 0),
        "licm_hoist" => (0, compiler_opt::licm::hoist(f).unwrap()),
        "licm_pipeline" => (
            compiler_opt::licm::canonicalize_preheaders(f).unwrap(),
            compiler_opt::licm::hoist(f).unwrap(),
        ),
        _ => panic!("invalid LICM case"),
    }
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 4, "ct03_licm_hotspot <case> <expressions> <iterations>");
    let case = args[1].as_str();
    let size: u32 = args[2].parse().unwrap();
    let iterations: usize = args[3].parse().unwrap();
    assert!((8..=512).contains(&size) && (1..=500).contains(&iterations));

    let mut input = fixture(size);
    if case == "licm_hoist" {
        assert_eq!(compiler_opt::licm::canonicalize_preheaders(&mut input).unwrap(), 1);
    }
    let mut expected = input.clone();
    let result = execute(case, &mut expected);
    verify(&expected).unwrap();
    assert_eq!(result.0, if case == "licm_hoist" { 0 } else { 1 });
    assert_eq!(result.1, if case == "licm_preheader" { 0 } else { size as usize + 1 });
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    format!("{expected:?}|{result:?}").hash(&mut hasher);
    let signature = hasher.finish();

    let start = Instant::now();
    for _ in 0..iterations {
        let mut work = black_box(input.clone());
        let observed = execute(case, &mut work);
        assert_eq!(observed, result);
        black_box((&work, observed));
    }
    let elapsed = start.elapsed().as_nanos();
    println!("CT04 case={case} size={size} iterations={iterations} ns={elapsed} signature={signature:016x}");
}
