// Braces and directive text in comments and literals are not scopes.
const char* mc002_text = "{ } #if COMMENT_TEXT";
const char* mc002_raw = R"scope(
{
#if RAW_TEXT
}
)scope";

#define MC002_OPEN_SCOPE() namespace mc002_macro_scope {
#define MC002_CLOSE_SCOPE() }

void Mc002FunctionScope() {
#if defined(FUNCTION_REGION)
  int value = 1;
#endif
}

class Mc002ClassScope {
#if defined(CLASS_REGION)
  int _value;
#endif
};

namespace mc002_namespace {
#if defined(NAMESPACE_REGION)
int value = 1;
#endif
}

MC002_OPEN_SCOPE()
#if defined(MACRO_REGION)
int mc002_macro_value = 1;
#endif
MC002_CLOSE_SCOPE()

#if defined(FILE_REGION)
int mc002_file_value = 1;
#endif
