/* Baseline: can native TypeId emit ordinary initialized data? */
int plain_global_a = 3;
int plain_global_b = 7;
int attribute_plain_globals(void) { return plain_global_a + plain_global_b; }
