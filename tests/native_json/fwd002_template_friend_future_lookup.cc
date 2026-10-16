template <typename T> class FutureTemplateTask;
class FutureTemplateOwner {
  template <typename T> friend class FutureTemplateTask;
};
FutureTemplateTask<int>* FutureTemplateLookup();
