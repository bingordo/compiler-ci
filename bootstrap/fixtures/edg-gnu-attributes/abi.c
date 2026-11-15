/* C frontend parsing does not establish target ABI lowering support. */
__attribute__((sysv_abi)) int attribute_sysv(int a, int b) { return a + b; }
int attribute_abi(void) { return attribute_sysv(3, 4); }
