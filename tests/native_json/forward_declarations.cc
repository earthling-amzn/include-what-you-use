namespace physical {
  class   RemoveMe  ;  // exact removable declaration
  struct   KeepMe  ;  // IWYU pragma: keep
  class   ExportMe  ;  // IWYU pragma: export
  class   Needed  ;  // IWYU pragma: keep

  template <
      typename T
  >
  class   MultiRemove  ;  // exact multiline removal

  template <
      typename T
  >
  struct   MultiKeep  ;  // IWYU pragma: keep
}

extern "C" {
  class   LinkageKept  ;
}

struct __attribute__((aligned (16))) AttributeKept  ;

class Outer {
  class   NestedKept  ;
};

physical::Needed* needed_forward_declaration;
