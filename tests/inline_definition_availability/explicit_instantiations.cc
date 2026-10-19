#include "explicit_api.inline.hpp"

template int Explicit<int>(int);
template int Explicit<long>(long);
template int ExplicitMemberBox<int>::Member(int);
template struct ExplicitClassBox<int>;

int main() {
  ExplicitMemberBox<int> member;
  ExplicitClassBox<int> box;
  return Explicit(1) == 2 && Explicit(2L) == 3 &&
                 member.Member(3) == 5 && box.Member(4) == 7
             ? 0
             : 1;
}
