#ifndef DEFAULT_PROVIDER_HPP
#define DEFAULT_PROVIDER_HPP
inline int MissingDefault();
inline int DefaultedCall(int value = MissingDefault()) { return value; }
#endif
