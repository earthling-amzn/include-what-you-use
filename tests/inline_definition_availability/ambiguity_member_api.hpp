#pragma once

template <class T>
struct AmbiguousMemberBox {
  int First(T);
  int Second(T, T);
};

extern template int AmbiguousMemberBox<int>::First(int);
extern template int AmbiguousMemberBox<int>::Second(int, int);
