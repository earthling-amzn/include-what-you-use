#include "defaulted_provider.hpp"
#include "defaulted_provider.inline.hpp"
int main() {
  LifecycleOwner first;
  LifecycleOwner copy(first);
  LifecycleOwner moved(static_cast<LifecycleOwner&&>(first));
  copy = moved;
  moved = static_cast<LifecycleOwner&&>(copy);
  return 0;
}
