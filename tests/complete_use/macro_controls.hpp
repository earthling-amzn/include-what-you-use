#ifndef TESTS_COMPLETE_USE_MACRO_CONTROLS_HPP_
#define TESTS_COMPLETE_USE_MACRO_CONTROLS_HPP_

class MacroArgumentControlValue;
class MacroBodyControlValue;

#define DECL_MACRO_BODY_CONTROL() \
  void MacroBodyControl(MacroBodyControlValue value)
#define DECL_MACRO_ARGUMENT_CONTROL(type) \
  void MacroArgumentControl(type value)

DECL_MACRO_BODY_CONTROL();
DECL_MACRO_ARGUMENT_CONTROL(MacroArgumentControlValue);

#include "macro_controls_provider.hpp"

#endif  // TESTS_COMPLETE_USE_MACRO_CONTROLS_HPP_
