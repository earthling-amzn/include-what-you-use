inline int MissingInstantiated();
template<class T> int InstantiatedOuter() { return MissingInstantiated(); }
int main() { return InstantiatedOuter<int>(); }
