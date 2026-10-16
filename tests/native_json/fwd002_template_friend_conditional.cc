#if ENABLE_TEMPLATE_FRIEND
namespace jobs {
template <typename T> class ConditionalTemplateTask;
}
class ConditionalTemplateOwner {
  template <typename T> friend class jobs::ConditionalTemplateTask;
};
#endif
