/* Baseline: isolate GNU inline asm support from naked annotation. */
int attribute_plain_asm(void) { __asm__ volatile("nop"); return 7; }
