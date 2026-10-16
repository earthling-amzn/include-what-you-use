#if defined(FILE_REGION)
int mc002_file_value = 1;
#endif

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

struct A {};
struct B {};
using Selected =
#if A
    A
#else
    B
#endif
    ;

void Mc002ParameterControl(
#if PARAMETER_A
    int value
#else
    long value
#endif
    );

template <
#if TEMPLATE_A
    typename T
#else
    typename T
#endif
    >
struct Mc002TemplateControl {};

extern "C"
#if LINKAGE_A
int Mc002LinkageControl();
#else
long Mc002LinkageControl();
#endif

int mc002_initializer_control =
#if INITIALIZER_A
    1
#else
    2
#endif
    ;
