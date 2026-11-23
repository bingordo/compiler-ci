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
            // Whole-TU, source-free ownership partition. This identifies
            // missing source image families independently of attribute
            // handling; it never prints SymbolIds, names or initializer bytes.
            // Use indexed owner sets to keep even large generated units linear.
            let defs:Vec<_>=globals.iter()
                .filter(|g|matches!(g.linkage,2|3)).collect();
            let weak:std::collections::HashSet<_>=il.global_materializations.iter()
                .filter(|m|m.is_weak).map(|m|m.symbol).collect();
            let int_images:std::collections::HashSet<_>=il.initializer_integers.iter()
                .map(|v|v.symbol).collect();
            let fp_images:std::collections::HashSet<_>=il.global_float_initializers.iter()
                .map(|v|v.symbol).collect();
            let agg_images:std::collections::HashSet<_>=il.global_aggregate_initializers.iter()
                .map(|v|v.symbol).collect();
            let image_owners:std::collections::HashSet<_>=int_images.iter()
                .chain(fp_images.iter()).chain(agg_images.iter()).copied().collect();
            let def_owners:std::collections::HashSet<_>=defs.iter()
                .map(|g|g.symbol).collect();
            let explicit:Vec<_>=defs.iter().copied()
                .filter(|g|g.explicit_initializer).collect();
            let missing:Vec<_>=explicit.iter().copied()
                .filter(|g|!image_owners.contains(&g.symbol)).collect();
            eprintln!(
                "EDG_PROBE_STATIC_OWNERS definitions={} explicit={} weak={} explicit_weak={} explicit_strong={} missing_image={} missing_weak={} missing_strong={} aggregate_weak={} aggregate_strong={} orphan_image_owners={} literal_reloc_owners={} routine_reloc_owners={}",
                defs.len(),explicit.len(),
                defs.iter().filter(|g|weak.contains(&g.symbol)).count(),
                explicit.iter().filter(|g|weak.contains(&g.symbol)).count(),
                explicit.iter().filter(|g|!weak.contains(&g.symbol)).count(),
                missing.len(),missing.iter().filter(|g|weak.contains(&g.symbol)).count(),
                missing.iter().filter(|g|!weak.contains(&g.symbol)).count(),
                agg_images.iter().filter(|id|weak.contains(*id)).count(),
                agg_images.iter().filter(|id|!weak.contains(*id)).count(),
                image_owners.iter().filter(|id|!def_owners.contains(*id)).count(),
                il.global_literal_relocations.iter()
                    .map(|r|r.symbol).collect::<std::collections::HashSet<_>>().len(),
                il.global_routine_relocations.iter()
                    .map(|r|r.symbol).collect::<std::collections::HashSet<_>>().len(),
            );
            // Classify only the *missing* weak static-image owner types.
            // Canonicalize original EDG typedef/qualifier chains (role 4);
            // every output is a bounded aggregate of numeric type/layout
            // facts, never source names, paths, symbol IDs or image bytes.
            let layouts:std::collections::HashMap<_,_>=il.type_object_sizes.iter()
                .map(|t|(t.owner,t)).collect();
            let mut groups=std::collections::BTreeMap::<(u32,u32,u64,u64),usize>::new();
            for global in missing.iter().filter(|g|weak.contains(&g.symbol)) {
                let original=symbol_types.get(&global.symbol).copied();
                let root_kind=original.and_then(|id|type_defs.get(&id))
                    .map_or(0,|t|t.kind);
                let mut native_kind=root_kind;
                let mut canonical=original;
                let mut visited=std::collections::HashSet::new();
                while let Some(id)=canonical {
                    if !visited.insert(id) {break;}
                    let Some(ty)=type_defs.get(&id) else {break;};
                    native_kind=ty.kind;
                    if ty.related_role!=Some(4) {break;}
                    canonical=ty.related;
                }
                let layout=canonical.and_then(|id|layouts.get(&id))
                    .or_else(||original.and_then(|id|layouts.get(&id)));
                let (bytes,align)=layout.map_or((0,0),|t|(t.bytes,t.alignment));
                *groups.entry((root_kind,native_kind,bytes,align)).or_default()+=1;
            }
            for ((root_kind,native_kind,bytes,align),count) in groups.iter().take(16) {
                println!(
                    "EDG_PROBE_MISSING_WEAK_TYPE root_kind={} native_kind={} bytes={} align={} count={}",
                    root_kind,native_kind,bytes,align,count
                );
            }
            println!("EDG_PROBE_MISSING_WEAK_TYPE_GROUPS total={} reported={} owners={}",
                groups.len(),groups.len().min(16),
                missing.iter().filter(|g|weak.contains(&g.symbol)).count());
            let symbol_types:std::collections::HashMap<_,_>=il.symbol_types.iter()
                .map(|t|(t.symbol,t.ty)).collect();
            let type_defs:std::collections::HashMap<_,_>=il.types.iter()
                .map(|t|(t.id,t)).collect();
            // Complete source-free owner taxonomy across *all* defined C
            // globals, not only the missing weak subset. Separate raw native
            // EDG type kind, verified image family and source relocation
            // ownership without emitting SymbolIds, names, paths or bytes.
            let literal_owner:std::collections::HashSet<_>=il.global_literal_relocations
                .iter().map(|r|r.symbol).collect();
            let routine_owner:std::collections::HashSet<_>=il.global_routine_relocations
                .iter().map(|r|r.symbol).collect();
            let data_owner:std::collections::HashSet<_>=il.global_aggregate_relocations
                .iter().map(|r|r.symbol).collect();
            let materializations:std::collections::HashMap<_,_>=
                il.global_materializations.iter().map(|m|(m.symbol,m)).collect();
            let mut kind_groups=std::collections::BTreeMap::<
                (u32,u32,u32,u32,u32,u32,u32,u32),usize>::new();
            for g in &defs {
                let kind=symbol_types.get(&g.symbol)
                    .and_then(|id|type_defs.get(id)).map_or(0,|t|t.kind);
                let image_membership=u32::from(int_images.contains(&g.symbol))+
                    u32::from(fp_images.contains(&g.symbol))+
                    u32::from(agg_images.contains(&g.symbol));
                let image=if image_membership>1 {4} else if int_images.contains(&g.symbol){1}
                    else if fp_images.contains(&g.symbol){2}
                    else if agg_images.contains(&g.symbol){3} else {0};
                let visibility=materializations.get(&g.symbol)
                    .and_then(|m|m.elf_visibility).unwrap_or(9);
                *kind_groups.entry((
                    kind,u32::from(weak.contains(&g.symbol)),
                    u32::from(g.explicit_initializer),image,
                    u32::from(literal_owner.contains(&g.symbol)),
                    u32::from(routine_owner.contains(&g.symbol)),
                    u32::from(data_owner.contains(&g.symbol)),visibility
                )).or_default()+=1;
            }
            for ((kind,weak,explicit,image,literal,routine,data,visibility),count)
                in kind_groups.iter().take(16) {
                println!("EDG_PROBE_STATIC_KIND kind={} weak={} explicit={} image={} literal={} routine={} data={} visibility={} count={}",
                    kind,weak,explicit,image,literal,routine,data,visibility,count);
            }
            println!("EDG_PROBE_STATIC_KIND_GROUPS total={} reported={} owners={}",
                kind_groups.len(),kind_groups.len().min(16),defs.len());
            // Independently classify every original data relocation *target*,
            // not just its owner. This identifies whether aggregate pointers
            // refer to source-proven scalar, other aggregate or imported data.
            let mut target_groups=std::collections::BTreeMap::<
                (u32,u32,u32,u32,u32,u32),usize>::new();
            for reloc in &il.global_aggregate_relocations {
                let target=reloc.target;
                let target_kind=symbol_types.get(&target)
                    .and_then(|id|type_defs.get(id)).map_or(0,|t|t.kind);
                let target_image=if int_images.contains(&target){1}
                    else if fp_images.contains(&target){2}
                    else if agg_images.contains(&target){3} else {0};
                *target_groups.entry((
                    u32::from(weak.contains(&reloc.symbol)),
                    target_kind,
                    u32::from(def_owners.contains(&target)),
                    u32::from(weak.contains(&target)),
                    target_image,
                    u32::from(reloc.offset%8==0 && reloc.addend==0),
                )).or_default()+=1;
            }
            for ((owner_weak,target_kind,defined,target_weak,image,slot_valid),count)
                in target_groups.iter().take(16) {
                println!("EDG_PROBE_DATA_TARGET owner_weak={} target_kind={} defined={} target_weak={} image={} slot_valid={} count={}",
                    owner_weak,target_kind,defined,target_weak,image,slot_valid,count);
            }
            println!("EDG_PROBE_DATA_TARGET_GROUPS total={} reported={} relocations={}",
                target_groups.len(),target_groups.len().min(16),
                il.global_aggregate_relocations.len());
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
            // A routine relocation must refer to an original EDG function
            // SymbolId, not an invented address or source-text spelling.
            // This counter is source-free, safe for private generated C.
            let mut routine_slots=std::collections::HashSet::new();
            let routine_valid=il.global_routine_relocations.iter().all(|reloc| {
                let image=il.global_aggregate_initializers.iter()
                    .find(|v|v.symbol==reloc.symbol);
                let zero=image.and_then(|v|{
                    let start=usize::try_from(reloc.offset).ok()?;
                    let end=start.checked_add(8)?;
                    v.bytes.get(start..end)
                }).is_some_and(|bytes|bytes.iter().all(|b|*b==0));
                let original=il.symbols.iter().any(|s|
                    s.id==reloc.routine && s.kind==1);
                let nonoverlap=routine_slots.insert((reloc.symbol,reloc.offset)) &&
                    !il.global_literal_relocations.iter().any(|r|
                        r.symbol==reloc.symbol && r.offset==reloc.offset) &&
                    !il.global_aggregate_relocations.iter().any(|r|
                        r.symbol==reloc.symbol && r.offset==reloc.offset);
                original && zero && nonoverlap && reloc.offset%8==0 &&
                    reloc.addend==0
            });
            println!("EDG_PROBE_STATIC_ROUTINES relocations={} valid={}",
                il.global_routine_relocations.len(),u8::from(routine_valid));
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
