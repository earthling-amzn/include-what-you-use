namespace jobs {
template <typename T> class GlobalTemplateTask;
}
class GlobalTemplateOwner {
  template <typename T> friend class ::jobs::GlobalTemplateTask;
};
