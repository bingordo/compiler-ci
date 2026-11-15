/* Separates custom code section metadata from global data. */
__attribute__((section(".text.edg_attr_probe"))) int attribute_section_function(void) { return 7; }
int attribute_section_user(void) { return attribute_section_function(); }
