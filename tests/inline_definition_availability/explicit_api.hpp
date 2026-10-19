#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_API_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_EXPLICIT_API_HPP_

template <typename T>
int Explicit(T value);

extern template int Explicit<int>(int);
extern template int Explicit<long>(long);

template <typename T>
struct ExplicitMemberBox {
  int Member(T value);
};

extern template int ExplicitMemberBox<int>::Member(int);

template <typename T>
struct ExplicitClassBox {
  int Member(T value);
};

extern template struct ExplicitClassBox<int>;

template <typename T>
int ConditionalExplicit(T value);

#if FEATURE
extern template int ConditionalExplicit<int>(int);
#endif

#endif
