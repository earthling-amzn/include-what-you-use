#ifndef TESTS_COMPLETE_USE_PROVIDER_HPP_
#define TESTS_COMPLETE_USE_PROVIDER_HPP_

class PointerDeclaration {};
class ReferenceDeclaration {};
class PointerDereference {
 public:
  int member;
};
class ReferenceMemberAccess {
 public:
  int member;
};
class AccessorDereference {
 public:
  int member;
};
class ByValueField {};
class ByValueParameter {};
class ByValueReturn {};
class BaseClass {};
class SizeofType {};
class NewType {};
class ScopedMember {
 public:
  static int member;
};
class PointerTemplateArgument {};

#endif  // TESTS_COMPLETE_USE_PROVIDER_HPP_
