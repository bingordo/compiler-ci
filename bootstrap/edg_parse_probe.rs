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
