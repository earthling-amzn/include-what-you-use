template <class T>
int ExplicitSameFile(T);

extern template int ExplicitSameFile<int>(int);

template <class T>
int ExplicitSameFile(T value) {
  return value + 8;
}

template int ExplicitSameFile<int>(int);

int main() {
  return ExplicitSameFile(1) == 9 ? 0 : 1;
}
