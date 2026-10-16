#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_API_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_API_HPP_

class InlineLifecycle {
 public:
  inline InlineLifecycle();
  inline ~InlineLifecycle();
  inline int Member() const;
};

inline int InlineFree(int value);

#endif
