  # include "tests/sem005/variant_provider.hpp" // retained physical

sem005_corpus::Handle* sem005_handle_address;
sem005_corpus::AliasHandle* sem005_alias_handle_address;
sem005_corpus::EnumClassHandle* sem005_enum_class_handle_address;
sem005_corpus::CompatibleRecord* sem005_compatible_record_address;
std::vector<int> sem005_values;

void sem005_local_collision() {
#if defined(SEM005_DEBUG_CONFIGURATION)
  class LocalCollision {};
#else
  union LocalCollision { int value; };
#endif
  LocalCollision* local = nullptr;
  (void)local;
}
