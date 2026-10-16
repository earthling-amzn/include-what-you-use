namespace jobs {
template <typename T> class NamespaceTemplateTask;
}
class NamespaceTemplateOwner {
  template <typename T> friend class jobs::NamespaceTemplateTask;
};
