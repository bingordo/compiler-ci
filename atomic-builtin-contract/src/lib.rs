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

    #[test]
    fn generated_mini_c_checked_builtin_plans_map_to_native_typeid_operations() {
        use compiler_ir::atomic_builtins::order_from_gnu_constant;
        use compiler_ir::{Op, ValueId};
        use mini_c_sema::{check_stream, Checker, CheckedExprKind, CheckedItem, CheckedStmt, Target};
        use mini_c_sema::builtins::BuiltinId as A;
        use mini_c_syntax::parser::Parser;

        let mut generated=0usize;
        for (c_type,bytes) in [
            ("unsigned char",1_u8),("unsigned short",2),("unsigned int",4),
            ("unsigned long",8),
        ] {
            for spec in REGISTRY.iter().filter(|s|s.id!=A::Ctz32) {
                let args=match spec.id {
                    A::AtomicThreadFence | A::AtomicSignalFence => "5".to_owned(),
                    A::AtomicLoadN => "p,5".to_owned(),
                    A::AtomicCompareExchangeN => "p,e,7,1,5,2".to_owned(),
                    _ => "p,7,5".to_owned(),
                };
                let source=format!("void f({c_type} *p, {c_type} *e) {{ {}({args}); }}",
                    spec.name);
                let mut parser=Parser::new(&source);
                let mut checker=Checker::new(Target::default()).unwrap();
                let mut items=Vec::new();
                check_stream(&mut parser, &mut checker, |item| {
                    items.push(item);
                    Ok(())
                }).unwrap_or_else(|error|panic!("Mini-C rejected generated source {source}: {error:?}"));
                let CheckedItem::Function(function)=&items[0] else {panic!("function")};
                let CheckedStmt::Block(statements)=&function.body else {panic!("block")};
                let CheckedStmt::Expr(Some(expression))=&statements[0] else {panic!("expr")};
                let CheckedExprKind::BuiltinAtomic(checked)=&expression.kind else {
                    panic!("registered atomic builtin bypassed checked source lowering: {source}");
                };
                assert_eq!(checked.builtin,spec.id);
                assert_eq!(checked.bytes,if checked.operands.is_empty(){0}else{bytes});
                let builtin=IrBuiltin::lookup(spec.name).expect("missing native builtin identity");
                let success=order_from_gnu_constant(u32::from(checked.success_order))
                    .expect("checked memory order cannot be invalid");
                let operands=match checked.operands.len() {
                    0=>vec![],
                    1=>vec![ValueId(0)],
                    2=>vec![ValueId(0),ValueId(2)],
                    3=>vec![ValueId(0),ValueId(1),ValueId(2)],
                    other=>panic!("unhandled checked atomic argument count {other}"),
                };
                let ir=if spec.id==A::AtomicCompareExchangeN {
                    let fail=checked.failure_order
                        .and_then(|v|order_from_gnu_constant(u32::from(v)))
                        .expect("CAS failure order must be checked");
                    builtin.checked_compare_exchange_typeid_op(
                        &operands,checked.bytes,checked.alignment,
                        checked.weak,success,fail)
                }else{
                    builtin.checked_typeid_op(
                        &operands,checked.bytes,checked.alignment,success)
                }.unwrap_or_else(|err|panic!("native checked builtin mismatch: {source}: {err}"));
                assert_eq!(ir.uses(),operands,"source/runtime operand mapping drifted: {source}");
                assert!(!ir.semantics().can_eliminate_if_unused());
                assert!(matches!(ir,Op::AtomicFence{..}|Op::AtomicLoad{..}|
                    Op::AtomicStore{..}|Op::AtomicRmw{..}|Op::AtomicCmpXchg{..}));
                generated+=1;
            }
        }
        assert_eq!(generated,4*(REGISTRY.len()-1));
    }
}
