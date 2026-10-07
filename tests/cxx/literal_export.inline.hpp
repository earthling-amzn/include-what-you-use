#ifndef LITERAL_EXPORT_INLINE_HPP
#define LITERAL_EXPORT_INLINE_HPP
constexpr const char* kCommentLike = "/*";
constexpr const char* kRawCommentLike = R"tag(/*)tag";
constexpr char kSlashCharacter = '/';
#if ENABLE_EXPORT_EDGE
#include "tests/cxx/literal_export.hpp"
#endif
inline int LiteralExport::Get() const { return 1; }
#endif
