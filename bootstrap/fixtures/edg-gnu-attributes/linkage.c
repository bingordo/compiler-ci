/* Linkage attributes affect ELF symbol binding and section placement. */
__attribute__((weak)) int attribute_weak = 3;
__attribute__((section(".data.edg_attr_probe"))) int attribute_section = 7;
int attribute_linkage(void) { return attribute_weak + attribute_section; }
