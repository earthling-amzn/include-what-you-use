#include "api.hpp"

using DecltypeControl = decltype(InlineLifecycle().Member() + InlineFree(0));
static_assert(sizeof(InlineLifecycle().Member()) == sizeof(int));
static_assert(noexcept(InlineFree(0)) || true);

int DefaultArgumentControl(int value = InlineFree(7)) { return value; }

int DiscardedConstexprControl() {
  if constexpr (false)
    return InlineFree(8);
  return 0;
}

class PureDispatchControl {
 public:
  virtual inline int Dispatch() = 0;
};

int CallPureDispatch(PureDispatchControl& value) {
  return value.Dispatch();
}

class PureDispatchImplementation final : public PureDispatchControl {
 public:
  int Dispatch() override { return 17; }
};

class DefaultedDependency {
 public:
  inline DefaultedDependency();
  inline ~DefaultedDependency();
};

class SpecialMemberControl {
 public:
  SpecialMemberControl() = default;
  SpecialMemberControl(const SpecialMemberControl&) = delete;
  ~SpecialMemberControl() = default;

 private:
  DefaultedDependency dependency_;
};

template <typename T>
inline int UninstantiatedInline(T value);

template <typename T>
int UninstantiatedControl(T value) {
  return UninstantiatedInline(value);
}

int main() {
  PureDispatchImplementation value;
  return CallPureDispatch(value) == 17 ? DiscardedConstexprControl() : 1;
}
