/* Minimal source-defined C scalar, without GNU attributes. */
int probe_scalar_global = 7;
int probe_scalar_read(void) { return probe_scalar_global; }
