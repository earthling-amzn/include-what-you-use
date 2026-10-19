struct TrivialLifecycle {
  int array[2];
  TrivialLifecycle() = default;
  ~TrivialLifecycle() = default;
  TrivialLifecycle(const TrivialLifecycle&) = default;
  TrivialLifecycle(TrivialLifecycle&&) = default;
  TrivialLifecycle& operator=(const TrivialLifecycle&) = default;
  TrivialLifecycle& operator=(TrivialLifecycle&&) = default;
};
int main() {
  TrivialLifecycle first;
  TrivialLifecycle copy(first);
  TrivialLifecycle moved(static_cast<TrivialLifecycle&&>(first));
  copy = moved;
  moved = static_cast<TrivialLifecycle&&>(copy);
  return 0;
}
