#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_API_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_API_HPP_

class InlineLifecycle {
 public:
  inline InlineLifecycle();
  inline ~InlineLifecycle();
  inline int Member() const;
  inline int AddressMember() const;
};

inline int InlineFree(int value);
inline int InlineAddress(int value);

template <typename T>
int InlineTemplate(T value);

template <>
inline int InlineTemplate<int>(int value);

#endif
