from conan import ConanFile
from conan.tools.cmake import CMake


class LockedExample(ConanFile):
    settings = "os", "arch", "compiler", "build_type"
    requires = "fmt/11.2.0"
    generators = "CMakeDeps", "CMakeToolchain"

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()
