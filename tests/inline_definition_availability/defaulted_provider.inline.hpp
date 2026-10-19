#ifndef DEFAULTED_PROVIDER_INLINE_HPP
#define DEFAULTED_PROVIDER_INLINE_HPP
#include "defaulted_provider.hpp"
inline LifecycleDependency::LifecycleDependency() = default;
inline LifecycleDependency::~LifecycleDependency() = default;
inline LifecycleDependency::LifecycleDependency(
    const LifecycleDependency&) = default;
inline LifecycleDependency::LifecycleDependency(
    LifecycleDependency&&) = default;
inline LifecycleDependency& LifecycleDependency::operator=(
    const LifecycleDependency&) = default;
inline LifecycleDependency& LifecycleDependency::operator=(
    LifecycleDependency&&) = default;
#endif
