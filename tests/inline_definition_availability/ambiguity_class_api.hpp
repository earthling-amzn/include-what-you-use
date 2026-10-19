#pragma once

namespace AmbiguousOne {
template <class T>
struct Box {
  int Get();
};
extern template struct Box<int>;
}  // namespace AmbiguousOne

namespace AmbiguousTwo {
template <class T>
struct Box {
  int Get();
};
extern template struct Box<int>;
}  // namespace AmbiguousTwo
