#ifndef TESTS_OCCURRENCE_RELOCATION_FIXTURE_HPP_
#define TESTS_OCCURRENCE_RELOCATION_FIXTURE_HPP_

  # include "tests/occurrence_relocation/provider.hpp" // retained physical

class OccurrenceRelocatedDeclaration; // retained physical

#if defined(COMPILER1)
	#	include "tests/occurrence_relocation/provider.hpp"	/* c1 physical */
	class OccurrenceRelocatedDeclaration;	/* c1 physical */
#endif

#if defined(COMPILER2)
#include "tests/occurrence_relocation/provider.hpp" /* c2 physical */
class OccurrenceRelocatedDeclaration; /* c2 physical */
#endif

OccurrenceRelocatedDeclaration* RelocatedDeclaration();
inline int RelocatedIncludeValue(OccurrenceRelocatedInclude* value) {
  return value->value;
}

#endif  // TESTS_OCCURRENCE_RELOCATION_FIXTURE_HPP_
