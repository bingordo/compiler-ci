//! Small bridge entry point for the compiler-ci EDG GNU-C audit.
//! The normal EDG example prints every IL node; doing so for 53+ MiB of
//! mrustc output can flood disk/logs and obscure where failures occurred.
//! This probe parses exactly one TU and emits only bounded counters.
//! Private input contents and source names are deliberately not reported.
use edgcpp::compile;

fn main() {
    let mut args = vec![String::from("edgcpfe")];
    args.extend(std::env::args().skip(1));
    if args.len() < 2 {
        eprintln!("EDG_PROBE_USAGE_ERROR");
        std::process::exit(2);
    }
    match compile(&args) {
        Ok(unit) => {
            println!(
                "EDG_PROBE_RESULT status={} types={} symbols={} routines={} statements={} expressions={}",
                unit.status,
                unit.il.types.len(),
                unit.il.symbols.len(),
                unit.il.routine_identities.len(),
                unit.il.statements.len(),
                unit.il.expressions.len(),
            );
            // Numeric-only census: no names, paths or generated C excerpts.
            // The TypeId whole-TU global proof currently distinguishes
            // imports, file-scope storage and nontrivial initializer trees.
            // Count those source facts *before* the adapter rejects a TU.
            let il=&unit.il;
            let globals=&il.global_variables;
            eprintln!(
                "EDG_PROBE_GLOBALS total={} externs={} internal={} file_scope={} explicit={} tls={} local_static={} materializations={} weak={} sections={} initializers={} integer_images={} fp_images={} aggregate_images={} relocations={} dynamic={} initializer_exprs={} lifetime_inits={} defined_routines={}",
                globals.len(),
                globals.iter().filter(|g|g.linkage==1).count(),
                globals.iter().filter(|g|g.linkage==2).count(),
                globals.iter().filter(|g|g.linkage==3).count(),
                globals.iter().filter(|g|g.explicit_initializer).count(),
                globals.iter().filter(|g|g.thread_local).count(),
                globals.iter().filter(|g|g.local_to_function).count(),
                il.global_materializations.len(),
                il.global_materializations.iter().filter(|m|m.is_weak).count(),
                il.global_materializations.iter().filter(|m|m.has_explicit_section).count(),
                il.variable_initializers.len(),
                il.initializer_integers.len(),
                il.global_float_initializers.len(),
                il.global_aggregate_initializers.len(),
                il.global_aggregate_relocations.len(),
                il.dynamic_initializers.len(),
                il.initializer_expressions.len(),
                il.initialization_lifetimes.len(),
                il.entities.iter().filter(|e|matches!(e,edgcpp::Entity::Routine{has_definition:true,..})).count()
            );
            // Literal definitions and slots must be a closed, consistent
            // snapshot before any backend may consume them. This source-free
            // census is allowed for private generated C; no literal bytes or
            // native names are printed.
            let mut literal_targets=std::collections::HashSet::new();
            let mut literal_valid=true;
            for object in &il.global_literal_objects {
                literal_valid &= literal_targets.insert(object.id) &&
                    object.id>0 && !object.bytes.is_empty() &&
                    object.bytes.len()<=4096 && object.bytes.last()==Some(&0);
            }
            for reloc in &il.global_literal_relocations {
                let image=il.global_aggregate_initializers.iter()
                    .find(|v|v.symbol==reloc.symbol);
                let checked=image.and_then(|v|{
                    let start=usize::try_from(reloc.offset).ok()?;
                    let end=start.checked_add(8)?;
                    v.bytes.get(start..end)
                }).is_some_and(|data|data.iter().all(|byte|*byte==0));
                if !literal_targets.contains(&reloc.literal_id) || !checked ||
                    reloc.addend!=0 {
                    literal_valid=false;
                }
            }
            println!("EDG_PROBE_STATIC_LITERALS objects={} relocations={} valid={}",
                il.global_literal_objects.len(),
                il.global_literal_relocations.len(),
                u8::from(literal_valid));
            if unit.status != 0 {
                std::process::exit(1);
            }
        }
        Err(_) => {
            eprintln!("EDG_PROBE_BRIDGE_ERROR");
            std::process::exit(2);
        }
    }
}
