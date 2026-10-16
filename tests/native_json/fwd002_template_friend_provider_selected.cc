#include "tests/native_json/fwd002_template_friend_provider.hpp"
template <typename T> class ProvidedTemplateTask;
class SelectedTemplateOwner {
  template <typename T> friend class ProvidedTemplateTask;
};
TemplateFriendProviderAnchor template_friend_provider_anchor;
