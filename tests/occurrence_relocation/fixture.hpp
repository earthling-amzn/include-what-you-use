#ifndef TESTS_OCCURRENCE_RELOCATION_FIXTURE_HPP_
#define TESTS_OCCURRENCE_RELOCATION_FIXTURE_HPP_

#include "tests/occurrence_relocation/provider.hpp"

class OccurrenceRelocatedDeclaration;

#if defined(COMPILER1)
#include "tests/occurrence_relocation/provider.hpp"
class OccurrenceRelocatedDeclaration;
#endif

#if defined(COMPILER2)
#include "tests/occurrence_relocation/provider.hpp"
class OccurrenceRelocatedDeclaration;
#endif

OccurrenceRelocatedDeclaration* RelocatedDeclaration();
inline int RelocatedIncludeValue(OccurrenceRelocatedInclude* value) {
  return value->value;
}

#endif  // TESTS_OCCURRENCE_RELOCATION_FIXTURE_HPP_
