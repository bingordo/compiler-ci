//! Exact before/after same-runner CT-03 TypeId SCCP optimizer benchmark.
//! This verified fixture compiles identically into both compiler revisions.
//! Measures folded known-constant SSA chains and unknown-entry no-op chains.
use compiler_ir::linkage::ModuleLinkage;
use compiler_ir::module_text::ModuleSnapshot;
use compiler_ir::signatures::legacy_function_signature;
use compiler_ir::typed_module::{DefinedSignature, TypedModule};
use compiler_ir::typed_ssa::TypedSsaModule;
use compiler_ir::types::{DataLayout, Endianness, PointerLayout, TypeTable};
use compiler_ir::{
    verify, Block, BlockId, Function, Instruction, Op, Parameter, Target,
    Terminator, Type, ValueId,
};
use std::hash::{Hash, Hasher};
use std::hint::black_box;
use std::time::Instant;

fn inst(id: u32, op: Op) -> Instruction {
    Instruction { id: ValueId(id), ty: op.result_type(), op }
}
fn target(to: u32, value: u32) -> Target {
    Target { block: BlockId(to), args: vec![ValueId(value)] }
}
fn chain(name: &str, count: u32, known: bool) -> Function {
    let mut blocks = vec![Block {
        id: BlockId(0),
        params: vec![Parameter { id: ValueId(0), ty: Type::I32 }],
        instructions: vec![
            inst(1, Op::ConstI32(1)),
            inst(2, Op::ConstI32(17)),
        ],
        end: Terminator::Jump(target(1, if known { 2 } else { 0 })),
    }];
    for bi in 1..=count {
        let value = 1000 + bi;
        let result = 2000 + bi;
        blocks.push(Block {
            id: BlockId(bi),
            params: vec![Parameter { id: ValueId(value), ty: Type::I32 }],
            instructions: vec![inst(
                result, Op::Add(ValueId(value), ValueId(1)),
            )],
            end: if bi == count {
                Terminator::Return(ValueId(result))
            } else {
                Terminator::Jump(target(bi + 1, result))
            },
        });
    }
    Function { name: name.into(), blocks }
}
fn typed(functions: Vec<Function>) -> TypedSsaModule {
    let mut types = TypeTable::new();
    let definitions = functions.iter().map(|f| DefinedSignature {
        symbol: f.name.clone(),
        signature: legacy_function_signature(f, &mut types).unwrap(),
    }).collect();
    let module = TypedModule {
        module: ModuleSnapshot {
            linkage: ModuleLinkage::all_external(&functions).unwrap(),
            functions,
            globals: vec![],
        },
        types,
        definitions,
        externals: vec![],
        layout: DataLayout::new(Endianness::Little)
            .with_pointer(0, PointerLayout { bits: 64, abi_alignment: 8 }).unwrap(),
    };
    TypedSsaModule::from_legacy(module).unwrap()
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 4, "usage: ct03_typed_sccp_hotspot <case> <blocks> <iterations>");
    let case = args[1].as_str();
    let size: u32 = args[2].parse().unwrap();
    let iterations: usize = args[3].parse().unwrap();
    assert!((2..=384).contains(&size) && (1..=500).contains(&iterations));
    let (known, count) = match case {
        "typed_sccp_fold" => (true, 1),
        "typed_sccp_noop" => (false, 1),
        "typed_sccp_many" => (false, 8),
        _ => panic!("unknown CT-03 case {case}"),
    };
    let functions = (0..count).map(|i| chain(
        &format!("ct03_typed_sccp_{case}_{i}"), size, known,
    )).collect::<Vec<_>>();
    for f in &functions { verify(f).unwrap(); }
    let fixture = typed(functions);
    fixture.validate().unwrap();

    let mut expected = fixture.clone();
    let result = compiler_opt::typed_sccp::propagate_typed(&mut expected).unwrap();
    expected.validate().unwrap();
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    format!("{expected:?}|{result:?}").hash(&mut hasher);
    let signature = hasher.finish();

    let start = Instant::now();
    for _ in 0..iterations {
        let mut work = black_box(fixture.clone());
        let output = compiler_opt::typed_sccp::propagate_typed(&mut work);
        assert!(output.is_ok());
        black_box((&work, &output));
    }
    let duration = start.elapsed().as_nanos();
    println!(
        "CT06 case={case} size={size} iterations={iterations} ns={duration} signature={signature:016x}"
    );
}
