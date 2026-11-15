/* A naked function requires target-specific instruction emission. */
__attribute__((naked)) void attribute_naked(void) { __asm__ volatile("ret"); }
