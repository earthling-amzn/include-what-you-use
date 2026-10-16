class A {
 public:
  virtual inline int f() = 0;
};

class B final : public A {
 public:
  int f() override { return 42; }
};

int main() {
  auto p = &A::f;
  B value;
  return (value.*p)() == 42 ? 0 : 1;
}
