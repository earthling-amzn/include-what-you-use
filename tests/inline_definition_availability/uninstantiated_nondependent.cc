inline int MissingUninstantiated();
template<class T> int UninstantiatedOuter() {
  return MissingUninstantiated();
}
int main() { return 0; }
