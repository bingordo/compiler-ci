// Copyright (c) 2026. Apache-2.0 WITH LLVM-exception.
// Independent GCC/Itanium ABI oracle. EDG may parse this exact source,
// but its source-owned logical index must never be mistaken for a
// physical address point or a secondary-vtable thunk without proof.
#include <cstddef>
#include <cstdint>
struct VRoot {
    virtual int root();
    virtual ~VRoot();
    int a;
};
struct VLeft {
    virtual int left();
    int b;
};
struct VRight {
    virtual int right();
    int c;
};
struct VDerived : VRoot, VLeft, VRight {
    int root() override;
    int left() override;
    int right() override;
    virtual int extra();
    int d;
};
int VRoot::root() { return 1; }
VRoot::~VRoot() {}
int VLeft::left() { return 2; }
int VRight::right() { return 3; }
int VDerived::root() { return 4; }
int VDerived::left() { return 5; }
int VDerived::right() { return 6; }
int VDerived::extra() { return 7; }
int main() {
    VDerived object{};
    VRoot *root=&object;
    VLeft *left=&object;
    VRight *right=&object;
    const auto *start=reinterpret_cast<const unsigned char*>(&object);
    const auto root_delta=
        reinterpret_cast<const unsigned char*>(root)-start;
    const auto left_delta=
        reinterpret_cast<const unsigned char*>(left)-start;
    const auto right_delta=
        reinterpret_cast<const unsigned char*>(right)-start;
    // Runtime and the independently dumped class layout must agree.
    if (sizeof(void*)!=8 || root_delta!=0 || left_delta!=16 ||
        right_delta!=32) return 1;
    if (root->root()!=4 || left->left()!=5 || right->right()!=6)
        return 2;
    return object.extra()!=7;
}
