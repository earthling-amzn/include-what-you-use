#ifndef ELSE_EXPORT_INLINE_HPP
#define ELSE_EXPORT_INLINE_HPP
#else
#include "tests/cxx/else_export.hpp"
inline int ElseExport::Get() const { return 1; }
#endif
