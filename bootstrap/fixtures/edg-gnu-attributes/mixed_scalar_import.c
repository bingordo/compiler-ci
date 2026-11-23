/* An ordinary C external DATA reference and an unrelated source-owned
 * initialized scalar must not be conflated into one storage category.
 * The generated ELF must define own_counter and leave imported_counter as
 * an undefined data symbol, never allocate it as zeroed local storage. */
extern int imported_counter;
int own_counter = 7;

int mixed_scalar_import(void) {
    return own_counter + imported_counter;
}
