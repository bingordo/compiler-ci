//! Executable x86-64 object emission from rustc or mrustc/GCC, through
//! the *production* TypeId SSA backend (no retired Function emitter).
//! The same bytes are compared across toolchains, GCC-linked and executed.

use compiler_backend::NativeTypeIdBackend;
use compiler_backend_x86::X86_64;
use compiler_ir::linkage::ModuleLinkage;
use compiler_ir::typed_cfg_builder::TypedCfgBuilder;
use compiler_ir::typed_module::DefinedSignature;
use compiler_ir::typed_ssa::{ResultType, TypedSsaModule};
use compiler_ir::types::{
    CallingConvention, DataLayout, Endianness, FunctionSignature, PointerLayout, TypeKind, TypeTable,
};
use compiler_ir::{Op, Terminator};

fn main() {
    let output_path = std::env::args().nth(1).expect("output ELF object path");
    let mut types = TypeTable::new();
    let i32_ty = types.intern(TypeKind::Integer(32)).expect("I32 TypeId");
    let mut cfg = TypedCfgBuilder::new();
    let (entry, _) = cfg.block(&[]);
    let value = cfg.emit(entry, Op::ConstI32(42), ResultType::Value(i32_ty));
    cfg.finish_block(entry, Terminator::Return(value))
        .expect("terminated entry block");
    let name = "bootstrap_x86_value";
    let function = cfg.finish(name.to_owned()).expect("native typed SSA");
    let module = TypedSsaModule {
        functions: vec![function],
        linkage: ModuleLinkage::all_external_names([name]).expect("ELF symbol linkage"),
        globals: vec![],
        types,
        layout: DataLayout::new(Endianness::Little)
            .with_pointer(0, PointerLayout { bits: 64, abi_alignment: 8 })
            .expect("SysV Ptr64 layout"),
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
    module.validate().expect("complete TypeId module/source ABI");
    let bytes = X86_64.emit_typeid_object(&module)
        .expect("production TypeId x86 ELF object emission");
    assert!(bytes.len() > 64, "ELF object has sufficient size");
    assert_eq!(&bytes[..4], b"\x7fELF", "ELF magic");
    assert_eq!(bytes[4], 2, "ELF64");
    assert_eq!(bytes[5], 1, "little-endian ELF");
    assert_eq!(bytes[18], 0x3e, "EM_X86_64");
    assert_eq!(bytes[19], 0, "EM_X86_64");
    std::fs::write(output_path, &bytes).expect("write emitted ELF object");
    println!("compiler-x86-object-smoke:{}", bytes.len());
}
