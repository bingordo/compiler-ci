//! Real x86-64 object emission from a compiler built via rustc or mrustc/GCC.
//! The generated ELF object is later linked and executed with a C harness.

use compiler_backend::Backend;
use compiler_backend_x86::X86_64;
use compiler_ir::{Block, BlockId, Function, Instruction, Op, Terminator, Type, ValueId};

fn main() {
    let output_path = std::env::args().nth(1).expect("output ELF object path");
    let function = Function {
        name: "bootstrap_x86_value".into(),
        blocks: vec![Block {
            id: BlockId(0),
            params: vec![],
            instructions: vec![Instruction {
                id: ValueId(0), ty: Type::I32, op: Op::ConstI32(42),
            }],
            end: Terminator::Return(ValueId(0)),
        }],
    };
    compiler_ir::verify(&function).expect("valid source SSA");
    let bytes = X86_64.emit_object(&[function]).expect("direct x86-64 ELF object emission");
    assert!(bytes.len() > 64, "ELF object has sufficient size");
    assert_eq!(&bytes[..4], b"\x7fELF", "ELF magic");
    assert_eq!(bytes[4], 2, "ELF64");
    assert_eq!(bytes[5], 1, "little-endian ELF");
    assert_eq!(bytes[18], 0x3e, "EM_X86_64");
    assert_eq!(bytes[19], 0, "EM_X86_64");
    std::fs::write(output_path, &bytes).expect("write emitted ELF object");
    println!("compiler-x86-object-smoke:{}", bytes.len());
}
