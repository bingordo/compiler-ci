/* Separates weak function linkage from initialized global storage. */
__attribute__((weak)) int attribute_weak_function(void) { return 3; }
int attribute_weak_user(void) { return attribute_weak_function(); }
