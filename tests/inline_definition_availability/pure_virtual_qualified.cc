class PureBase {
 public:
  virtual inline int MissingBody() = 0;
};

class QualifiedCaller final : public PureBase {
 public:
  int MissingBody() override { return PureBase::MissingBody(); }
};

int main() {
  QualifiedCaller value;
  return value.MissingBody();
}
