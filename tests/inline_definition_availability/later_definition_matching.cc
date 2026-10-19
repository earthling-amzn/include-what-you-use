inline int LaterMatching();
template <typename T> int LaterTemplateMatching(T);

#if FEATURE
int CallLaterMatching() {
  return LaterMatching() + LaterTemplateMatching(1);
}
inline int LaterMatching() { return 2; }
template <typename T>
int LaterTemplateMatching(T value) { return static_cast<int>(value) + 3; }
#endif

int main() {
#if FEATURE
  return CallLaterMatching() == 6 ? 0 : 1;
#else
  return 0;
#endif
}
