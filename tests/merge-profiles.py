#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Merge profiles captured by native Conan on another CI architecture."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from conan_purifier import lock

root = REPO / "examples/minimal"
companion = root / "conan-purifier.lock.json"
native = root / "conan.lock"
data = json.loads(companion.read_text())
assert not lock.problems(root, native, data)
for argument in sys.argv[1:]:
    incoming = json.loads(Path(argument).read_text())
    assert incoming["inputs"] == data["inputs"]
    assert incoming["manifests"] == data["manifests"]
    with tempfile.TemporaryDirectory() as temporary:
        check = Path(temporary) / "conan.lock"
        check.write_text(incoming["nativeLock"])
        assert not lock.problems(root, check, incoming)
    data["profiles"].update(incoming["profiles"])
text = lock.native_text(lock.union(data["profiles"], data["activeProfile"]))
data["nativeLock"] = text
data["nativeHash"] = hashlib.sha256(text.encode()).hexdigest()
data = lock.seal(data)
with tempfile.TemporaryDirectory() as temporary:
    check = Path(temporary) / "conan.lock"
    check.write_text(text)
    assert not lock.problems(root, check, data)
native.write_text(text)
companion.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
