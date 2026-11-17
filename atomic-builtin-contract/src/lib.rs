//! Cross-repository source/compiler builtin-contract parity.
//! The Mini-C syntax and semantic layers cannot depend on compiler-ir, so
//! test their independent published registries together in compiler-ci.
#[cfg(test)]
mod tests {
    use compiler_ir::builtins::{Builtin as IrBuiltin, BuiltinLowering};
    use mini_c_sema::builtins::{BuiltinId, LoweringClass, REGISTRY};

    #[test]
    fn mini_c_and_compiler_ir_builtin_signatures_remain_in_lockstep() {
        for frontend in REGISTRY {
            let ir = IrBuiltin::lookup(frontend.name).unwrap_or_else(|| {
                panic!("Mini-C builtin {} has no corresponding compiler IR descriptor",
                    frontend.name)
            });
            let descriptor = ir.signature();
            assert_eq!(descriptor.source_argument_count, frontend.argument_count,
                "{}: argument count mismatch", frontend.name);
            assert_eq!(descriptor.order_argument, frontend.memory_order_argument,
                "{}: ordering argument mismatch", frontend.name);
            assert_eq!(descriptor.failure_order_argument, frontend.failure_order_argument,
                "{}: CAS failure-order argument mismatch", frontend.name);
            assert_eq!(descriptor.weak_argument, frontend.weak_argument,
                "{}: CAS weak argument mismatch", frontend.name);
            match frontend.lowering {
                LoweringClass::IntegerCtz => {
                    assert_eq!(frontend.id, BuiltinId::Ctz32);
                    assert_eq!(descriptor.lowering, BuiltinLowering::FrontendCtz);
                }
                LoweringClass::AtomicIrRequired => {
                    assert!(matches!(descriptor.lowering,
                        BuiltinLowering::NativeAtomic | BuiltinLowering::DeferredAtomic),
                        "{}: atomic builtin must not degrade to an ordinary call",
                        frontend.name);
                }
            }
        }
        assert_eq!(REGISTRY.len(), 12, "update the parity fixture on additions");
    }
}
