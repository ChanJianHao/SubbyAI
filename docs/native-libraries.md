# Native libraries and modification

Release downloads include `SubbyAI-native-sources-1.0.0.zip` beside the binaries, with exact
Qt 6.11.2 and PySide/shiboken 6.11.2 source archives and their upstream SHA-256 hashes.
These libraries are unmodified. The complete QtBase source includes its bundled component
sources, license files and platform build scripts. Applicable licenses are included in the app.

The application uses dynamically linked Qt Core, GUI, Widgets, Network and OpenGL. Compatible
replacement libraries can be placed in the portable app's `_internal` Qt/PySide library directory
(or the equivalent macOS bundle directory). Keep architecture and ABI compatible. Reverse
engineering for debugging modifications to these libraries is permitted.

To build replacements, extract the corresponding archives. Use CMake/Ninja and the platform
compiler supported by Qt 6.11: Visual Studio 2022 on Windows or Xcode on macOS. Configure QtBase
as a **shared**, release, open-source build, with a private installation prefix, and build/install
it. For example, from a developer shell:

```text
configure -release -shared -opensource -confirm-license -nomake examples -nomake tests -prefix <qt-prefix>
cmake --build . --parallel
cmake --install .
```

Build PySide/shiboken from the matching source with its included `setup.py` and `build_scripts`,
using the new Qt prefix/qmake and the project's pinned Python version. The source archive includes
the supported build options; see [Qt for Python build documentation](https://doc.qt.io/qtforpython-6/building_from_source/index.html).
Rebuild the application using [development.md](development.md) and run its frozen self-check.

Frozen SubbyAI captures NumPy audio frames; it does not distribute PyAV or FFmpeg. A reviewed,
version/hash-guarded staging step makes faster-whisper's file-decoding imports lazy. Live NumPy
recognition is unchanged. The source environment still installs the upstream file-decoding
dependency, so use its own notices when independently distributing a different application.

Optional NVIDIA runtime wheels retain their vendor redistribution terms and shipped notices.
They are not covered by SubbyAI's MIT license. Other runtime notices and exact versions are
collected in the application's `licenses` directory. Downloaded speech/translation models have
their own licenses and are not included in the application binaries.
