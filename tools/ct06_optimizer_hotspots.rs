//! CT-06 isolated optimizer hotspot replay, injected *identically* into
//! before/after compiler-opt checkouts by compiler-ci.
//!
//! Deliberately adversarial valid SSA, not an end-to-end C benchmark.
//! The same real verifier and SCCP/mem2reg implementations run in each build.
use compiler_ir::{
    verify, Block, BlockId, Function, Instruction, Op, Parameter, Target,
    Terminator, Type, ValueId,
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
fn edge(block: u32, args: &[u32]) -> Target {
    Target {
        block: BlockId(block),
        args: args.iter().copied().map(ValueId).collect(),
    }
}

/// One constant and one +1 step propagate through block parameters.
/// Forward order is the favorable control for the former sweep algorithm;
/// reverse order reveals its repeated full-CFG convergence costs.
fn sccp_chain(n: u32, reverse: bool) -> Function {
    let mut blocks = vec![Block {
        id: BlockId(0),
        params: vec![],
        instructions: vec![
            inst(1, Op::ConstI32(5)),
            inst(2, Op::ConstI32(1)),
        ],
        end: Terminator::Jump(edge(if reverse { n } else { 1 }, &[1])),
    }];
    for bi in 1..=n {
        let incoming = 1000 + bi;
        let result = 2000 + bi;
        let last = if reverse { bi == 1 } else { bi == n };
        blocks.push(Block {
            id: BlockId(bi),
            params: vec![p(incoming, Type::I32)],
            instructions: vec![inst(
                result, Op::Add(ValueId(incoming), ValueId(2)),
            )],
            end: if last {
                Terminator::Return(ValueId(result))
            } else {
                Terminator::Jump(edge(
                    if reverse { bi - 1 } else { bi + 1 }, &[result],
                ))
            },
        });
    }
    Function {
        name: if reverse { "ct06_sccp_reverse" } else { "ct06_sccp_forward" }.into(),
        blocks,
    }
}

/// Dependent scalar locals with nonescaping addresses. Before CT-03c every
/// promoted slot forced a full-function operand rewrite; after CT-03c
/// aliases are compressed and rewritten once. This is the real IR shape
/// used by the unit/native parity checks, extended to larger sizes.
fn mem2reg_slots(n: u32) -> Function {
    let mut instructions = vec![inst(1, Op::ConstI32(1))];
    let mut next = 2u32;
    let mut previous = ValueId(0);
    for slot in 1..=n {
        let addr = ValueId(next);
        instructions.push(inst(next, Op::LocalAddr(slot)));
        next += 1;
        instructions.push(inst(next, Op::StoreI32(addr, previous)));
        next += 1;
        let loaded = ValueId(next);
        instructions.push(inst(next, Op::LoadI32(addr)));
        next += 1;
        let sum = ValueId(next);
        instructions.push(inst(next, Op::Add(loaded, ValueId(1))));
        next += 1;
        previous = sum;
    }
    Function {
        name: "ct06_mem2reg_slots".into(),
        blocks: vec![Block {
            id: BlockId(0),
            params: vec![p(0, Type::I32)],
            instructions,
            end: Terminator::Return(previous),
        }],
    }
}

fn run(case: &str, f: &mut Function) -> Result<String, String> {
    match case {
        "sccp_reverse" | "sccp_forward" => {
            Ok(format!("{:?}", compiler_opt::sccp::propagate(f)?))
        }
        "mem2reg_slots" => {
            Ok(format!("{}", compiler_opt::mem2reg::promote_nonescaping_locals(f)?))
        }
        _ => panic!("unknown CT-06 fixture {case}"),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 4, "usage: ct06_optimizer_hotspots <case> <size> <iterations>");
    let case = args[1].as_str();
    let size: u32 = args[2].parse().expect("positive fixture size");
    let iterations: usize = args[3].parse().expect("positive iterations");
    assert!((2..=384).contains(&size) && (1..=500).contains(&iterations));

    let fixture = match case {
        "sccp_reverse" => sccp_chain(size, true),
        "sccp_forward" => sccp_chain(size, false),
        "mem2reg_slots" => mem2reg_slots(size),
        _ => panic!("unknown case"),
    };
    verify(&fixture).expect("fixture must be verifier-valid before optimization");
    let mut expected = fixture.clone();
    let result = run(case, &mut expected).expect("pass under test failed");
    verify(&expected).expect("pass changed verifier validity");
    // Deterministic, exact debug representation plus resulting pass statistics;
    // no timing/rank is used for correctness. Keep the full signature stable
    // across the two independently compiled compiler revisions.
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    format!("{expected:?}|{result}").hash(&mut hasher);
    let signature = hasher.finish();

    let start = Instant::now();
    for _ in 0..iterations {
        let mut work = black_box(fixture.clone());
        let result = run(case, &mut work);
        black_box((&work, &result));
        assert!(result.is_ok(), "timed optimizer invocation failed");
    }
    let duration = start.elapsed().as_nanos();
    println!(
        "CT06 case={case} size={size} iterations={iterations} ns={duration} signature={signature:016x}"
    );
}
