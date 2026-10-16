#include "tests/native_json/fwd002_template_friend_provider.hpp"
template <typename T> class ProvidedTemplateTask;
class RemovedTemplateOwner {
  template <typename T> friend class ProvidedTemplateTask;
};
