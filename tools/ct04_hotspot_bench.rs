//! Reproducible CT-04 IR/optimizer benchmark, injected unchanged into the
//! before/after compiler-opt example directory by compiler-ci.
//!
//! This is NOT a source-to-ELF benchmark. Each timed observation clones a
//! verified fixture and runs the real IR verifier or phi simplifier; its
//! output signature is compared with the other compiler revision.
use compiler_ir::{
    verify, Block, BlockId, Function, Instruction, Op, Parameter, Target, Terminator, Type, ValueId,
};
use std::hash::{Hash, Hasher};
use std::hint::black_box;
use std::time::Instant;

fn p(id: u32, ty: Type) -> Parameter {
    Parameter { id: ValueId(id), ty }
}

fn inst(id: u32, op: Op) -> Instruction {
    Instruction { id: ValueId(id), ty: op.result_type(), op }
}

fn edge(to: u32, ids: &[u32]) -> Target {
    Target {
        block: BlockId(to),
        args: ids.iter().copied().map(ValueId).collect(),
    }
}

// Reverse storage order forces propagation in the least favorable direction
// for repeated linear scans. Each phi has a single forwarding origin.
fn phi_reverse(n: u32) -> Function {
    let mut blocks = vec![Block {
        id: BlockId(0),
        params: vec![p(0, Type::I32)],
        instructions: vec![],
        end: Terminator::Jump(edge(n, &[0])),
    }];
    for index in 1..=n {
        let id = 1000 + index;
        blocks.push(Block {
            id: BlockId(index),
            params: vec![p(id, Type::I32)],
            instructions: vec![],
            end: if index == 1 {
                Terminator::Return(ValueId(id))
            } else {
                Terminator::Jump(edge(index - 1, &[id]))
            },
        });
    }
    Function { name: "ct04_phi_reverse".into(), blocks }
}

// Two distinct CFG edges with identical arguments for each phi. This stresses
// the total phi count (not just block count), preserving edge multiplicity.
fn phi_wide(n: u32) -> Function {
    let entry_args: Vec<u32> = (0..n).collect();
    let join_ids: Vec<u32> = (0..n).map(|i| 10000 + i).collect();
    Function {
        name: "ct04_phi_wide".into(),
        blocks: vec![
            Block {
                id: BlockId(0),
                params: entry_args.iter().map(|&id| p(id, Type::I32)).collect(),
                instructions: vec![inst(8000, Op::ConstBool(true))],
                end: Terminator::Branch {
                    condition: ValueId(8000),
                    yes: edge(1, &entry_args),
                    no: edge(1, &entry_args),
                },
            },
            Block {
                id: BlockId(1),
                params: join_ids.iter().map(|&id| p(id, Type::I32)).collect(),
                instructions: vec![],
                end: Terminator::Return(ValueId(join_ids[0])),
            },
        ],
    }
}

fn pointer_reverse(n: u32, unknown: bool) -> Function {
    let allocation = if unknown {
        Op::LocalAddr(1)
    } else {
        Op::LocalArrayAddr { base_slot: 32, length: 16 }
    };
    let mut blocks = vec![Block {
        id: BlockId(0),
        params: vec![p(0, Type::I32)],
        instructions: vec![inst(1, allocation)],
        end: Terminator::Jump(edge(n, &[1])),
    }];
    for index in 1..=n {
        let id = 1000 + index;
        let (instructions, end) = if index == 1 {
            (
                vec![
                    inst(5000, Op::ArrayElemAddr {
                        base: ValueId(id), index: ValueId(0), length: 16,
                    }),
                    inst(5001, Op::LoadI32(ValueId(5000))),
                ],
                Terminator::Return(ValueId(5001)),
            )
        } else {
            (vec![], Terminator::Jump(edge(index - 1, &[id])))
        };
        blocks.push(Block {
            id: BlockId(index),
            params: vec![p(id, Type::Ptr64)],
            instructions,
            end,
        });
    }
    Function { name: "ct04_pointer_reverse".into(), blocks }
}

// Parallel pointer phis at a join take a meet of distinct proven roots.
fn pointer_wide(n: u32) -> Function {
    let left = vec![1; n as usize];
    let right = vec![2; n as usize];
    let mut insts = Vec::new();
    for index in 0..n {
        insts.push(inst(10000 + index, Op::ArrayElemAddr {
            base: ValueId(1000 + index), index: ValueId(0), length: 16,
        }));
    }
    insts.push(inst(20000, Op::LoadI32(ValueId(10000))));
    Function {
        name: "ct04_pointer_wide".into(),
        blocks: vec![
            Block {
                id: BlockId(0),
                params: vec![p(0, Type::I32)],
                instructions: vec![
                    inst(1, Op::LocalArrayAddr { base_slot: 32, length: 16 }),
                    inst(2, Op::LocalArrayAddr { base_slot: 64, length: 16 }),
                    inst(3, Op::ConstBool(true)),
                ],
                end: Terminator::Branch {
                    condition: ValueId(3), yes: edge(1, &[]), no: edge(2, &[]),
                },
            },
            Block {
                id: BlockId(1), params: vec![], instructions: vec![],
                end: Terminator::Jump(edge(3, &left)),
            },
            Block {
                id: BlockId(2), params: vec![], instructions: vec![],
                end: Terminator::Jump(edge(3, &right)),
            },
            Block {
                id: BlockId(3),
                params: (0..n).map(|i| p(1000 + i, Type::Ptr64)).collect(),
                instructions: insts,
                end: Terminator::Return(ValueId(20000)),
            },
        ],
    }
}

fn run(case: &str, function: &mut Function) -> Result<usize, String> {
    match case {
        "phi_reverse" | "phi_wide" => compiler_opt::phi::simplify_block_parameters(function),
        "pointer_reverse" | "pointer_unknown" | "pointer_wide" => verify(function).map(|()| 0),
        _ => panic!("unknown CT-04 benchmark case {case}"),
    }
}

fn main() {
    let args: Vec<_> = std::env::args().collect();
    assert_eq!(args.len(), 4, "usage: ct04_hotspot_bench <case> <size> <iterations>");
    let case = args[1].as_str();
    let size: u32 = args[2].parse().expect("positive block/phi count");
    let iterations: usize = args[3].parse().expect("iteration count");
    assert!((2..=1024).contains(&size) && (1..=500).contains(&iterations));

    let fixture = match case {
        "phi_reverse" => phi_reverse(size),
        "phi_wide" => phi_wide(size),
        "pointer_reverse" => pointer_reverse(size, false),
        "pointer_unknown" => pointer_reverse(size, true),
        "pointer_wide" => pointer_wide(size),
        _ => panic!("unknown case"),
    };
    // Ensure the fixture itself is valid unless this explicitly tests
    // rejecting an ABI/unknown-pointer origin.
    let checked = verify(&fixture);
    if case == "pointer_unknown" {
        assert!(checked.as_ref().is_err_and(|error|
            error.contains("proven allocation extent")));
    } else {
        checked.expect("benchmark must start from valid typed SSA");
    }

    let mut warmup = fixture.clone();
    let expected_result = run(case, &mut warmup);
    if case == "pointer_unknown" {
        assert!(expected_result.is_err());
    } else {
        expected_result.as_ref().expect("worklist should preserve valid SSA");
        verify(&warmup).expect("post-optimization CFG must verify");
    }
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    format!("{warmup:?}|{expected_result:?}").hash(&mut hasher);
    let signature = hasher.finish();

    let start = Instant::now();
    for _ in 0..iterations {
        let mut f = black_box(fixture.clone());
        let result = run(case, &mut f);
        black_box((&f, &result));
        assert_eq!(result.is_ok(), expected_result.is_ok());
    }
    let duration = start.elapsed().as_nanos();
    println!(
        "CT04 case={case} size={size} iterations={iterations} ns={duration} signature={signature:016x}"
    );
}
