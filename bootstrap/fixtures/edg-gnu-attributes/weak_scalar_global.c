/* Same scalar as scalar_global.c, but source requires STB_WEAK. */
__attribute__((weak)) int probe_scalar_global = 7;
int probe_scalar_read(void) { return probe_scalar_global; }
