template <typename T> int LaterTemplateMismatched(T);

int CallLaterMismatched() {
  return LaterTemplateMismatched(1);
}
#if FEATURE
template <typename T>
int LaterTemplateMismatched(T value) { return static_cast<int>(value); }
#endif

int main() { return CallLaterMismatched() == 1 ? 0 : 1; }
