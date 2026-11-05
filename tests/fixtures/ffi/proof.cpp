#include <fmt/core.h>
#include <string>
extern "C" int answer(void) { return std::stoi(fmt::format("{}", 42)); }
