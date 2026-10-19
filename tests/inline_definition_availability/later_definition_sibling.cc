template <typename T> int LaterTemplateSibling(T);

#if FEATURE
int CallLaterSibling() { return LaterTemplateSibling(1); }
#else
template <typename T>
int LaterTemplateSibling(T value) { return static_cast<int>(value); }
#endif

int main() {
#if FEATURE
  return 0;
#else
  return LaterTemplateSibling(1) == 1 ? 0 : 1;
#endif
}
