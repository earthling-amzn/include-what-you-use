#pragma once

inline int NestedEdgeLeaf();

struct NestedEdgeItem {
  inline NestedEdgeItem();
  inline ~NestedEdgeItem();
};

struct NestedEdgeBox {
  int value;
  inline NestedEdgeBox();
};

inline int NestedUsesDefault(int value = NestedEdgeLeaf());
inline int NestedDefaultOuter();
inline int NestedLifecycleOuter();
inline int NestedLambdaPointerOuter();

struct NestedDefaultedDmi {
  int value = NestedUsesDefault();
  NestedDefaultedDmi() = default;
};

struct NestedExplicitDmi {
  int value = NestedUsesDefault();
  inline NestedExplicitDmi();
};

inline int NestedDmiOuter();
inline int NestedGenericLambdaOuter();
