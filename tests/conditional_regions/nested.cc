#if defined(OUTER_BRANCH)
#if defined(INNER_BRANCH)
int mc002_nested_value = 1;
#elif defined(INNER_ALTERNATIVE)
int mc002_nested_value = 2;
#else
int mc002_nested_value = 3;
#endif
#elif defined(OUTER_ALTERNATIVE)
int mc002_nested_value = 4;
#else
int mc002_nested_value = 5;
#endif
