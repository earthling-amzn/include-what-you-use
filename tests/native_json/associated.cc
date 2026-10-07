#include "tests/native_json/associated.h"
NativeJsonAssociatedDep AssociatedValue(const NativeJsonAssociated& value) {
  return value.value;
}
