#ifndef DIGRAPH_EXPORT_INLINE_HPP
#define DIGRAPH_EXPORT_INLINE_HPP
%:if ENABLE_EXPORT_EDGE
%:include "tests/cxx/digraph_export.hpp"
%:endif
inline int DigraphExport::Get() const { return 1; }
#endif
