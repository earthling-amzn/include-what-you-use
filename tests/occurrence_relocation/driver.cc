#include "tests/occurrence_relocation/fixture.hpp"  // IWYU pragma: keep

int OccurrenceRelocationCompile() {
  return RelocatedDeclaration() != nullptr;
}
