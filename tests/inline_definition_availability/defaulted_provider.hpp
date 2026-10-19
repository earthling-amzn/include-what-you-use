#ifndef DEFAULTED_PROVIDER_HPP
#define DEFAULTED_PROVIDER_HPP
struct LifecycleDependency {
  inline LifecycleDependency();
  inline ~LifecycleDependency();
  inline LifecycleDependency(const LifecycleDependency&);
  inline LifecycleDependency(LifecycleDependency&&);
  inline LifecycleDependency& operator=(const LifecycleDependency&);
  inline LifecycleDependency& operator=(LifecycleDependency&&);
  int value;
};
struct LifecycleBase {
  LifecycleDependency base;
  LifecycleBase() = default;
  ~LifecycleBase() = default;
  LifecycleBase(const LifecycleBase&) = default;
  LifecycleBase(LifecycleBase&&) = default;
  LifecycleBase& operator=(const LifecycleBase&) = default;
  LifecycleBase& operator=(LifecycleBase&&) = default;
};
struct LifecycleOwner : LifecycleBase {
  LifecycleDependency array[2];
  LifecycleDependency initialized{};
  int trivial = 0;
  LifecycleOwner() = default;
  ~LifecycleOwner() = default;
  LifecycleOwner(const LifecycleOwner&) = default;
  LifecycleOwner(LifecycleOwner&&) = default;
  LifecycleOwner& operator=(const LifecycleOwner&) = default;
  LifecycleOwner& operator=(LifecycleOwner&&) = default;
};
#endif
