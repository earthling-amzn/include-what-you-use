template<class T> int LateSame(T);
int main() { return LateSame(1); }
template<class T> int LateSame(T value) { return value - 1; }
