template <typename T> class TemplateTask;
class TemplateOwner { template <typename T> friend class TemplateTask; };
