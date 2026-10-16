#ifndef TESTS_SEM005_DECLARATION_KINDS_HPP_
#define TESTS_SEM005_DECLARATION_KINDS_HPP_

namespace sem005_kinds {

class ClassKind {};
struct StructKind {};
union UnionKind { int value; };
enum class ScopedEnumKind { value };
enum UnscopedEnumKind { unscoped_value };
template <typename T> class ClassTemplateKind {};
typedef int TypedefKind;
using UsingAliasKind = int;

}  // namespace sem005_kinds

#endif  // TESTS_SEM005_DECLARATION_KINDS_HPP_
