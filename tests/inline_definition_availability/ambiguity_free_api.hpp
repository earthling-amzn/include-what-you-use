#pragma once

template <class T>
int AmbiguousClash(T);
template <class T>
int AmbiguousClash(T, T);

extern template int AmbiguousClash<int>(int);
extern template int AmbiguousClash<int>(int, int);
