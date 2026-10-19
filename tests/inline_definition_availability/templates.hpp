#ifndef TESTS_INLINE_DEFINITION_AVAILABILITY_TEMPLATES_HPP_
#define TESTS_INLINE_DEFINITION_AVAILABILITY_TEMPLATES_HPP_

template <typename T>
int TemplateFunction(T value);

template <typename T>
struct TemplateBox {
  TemplateBox();
  ~TemplateBox();
  int Member() const;
  int AddressMember() const;

  template <typename U>
  int MemberTemplate(U value) const;
};

template <typename T>
int InstantiateDependent(T& value) {
  return value.Member();
}

template <typename T>
int UninstantiatedDependent(T& value) {
  return value.NeverResolved();
}

#endif
