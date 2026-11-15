/* Unlike sizeof(type), an over-aligned object tests emitted storage. */
struct __attribute__((aligned(32))) OveralignedGlobal { int value; };
struct OveralignedGlobal attribute_aligned_global = { 3 };
_Static_assert(_Alignof(struct OveralignedGlobal) == 32, "overaligned native type");
int attribute_aligned_global_value(void) { return attribute_aligned_global.value; }
