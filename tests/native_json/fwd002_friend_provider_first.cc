#include "tests/native_json/fwd002_friend_provider.hpp"
class Task;
class Owner { friend class Task; };
FriendProviderAnchor friend_provider_anchor;
