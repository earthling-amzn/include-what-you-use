struct PureDestructorBase { virtual inline ~PureDestructorBase() = 0; };
struct PureDestructorDerived : PureDestructorBase {
  ~PureDestructorDerived() override = default;
};
int main() { PureDestructorDerived value; }
