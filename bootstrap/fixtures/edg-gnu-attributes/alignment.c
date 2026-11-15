/* GNU alignment is ABI-significant, not an ignorable annotation. */
struct __attribute__((aligned(32))) Aligned { char bytes[3]; };
struct __attribute__((packed)) Packed { char tag; int value; };
_Static_assert(_Alignof(struct Aligned) == 32, "aligned type must be 32-byte aligned");
_Static_assert(sizeof(struct Aligned) == 32, "aligned type must be 32 bytes");
_Static_assert(sizeof(struct Packed) == 5, "packed type must not contain padding");
int attribute_layout(void) { return (int)sizeof(struct Packed); }
