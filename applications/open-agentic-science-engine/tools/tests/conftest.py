# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared pytest configuration for the tools test suite.

Guards against a class of test-isolation bugs: replacing a core dependency
(``click``, ``yaml``, ``requests``) in ``sys.modules`` with a mock and not
restoring it. Pytest imports every test module during collection, so a
module-level ``patch.dict("sys.modules", ...).start()`` without a matching
``stop()`` stays active for the whole run. It then breaks or silently
neuters unrelated tests that import those packages later.

These packages are hard dependencies of ``oase``. Tests must patch them
where they are used (e.g. ``patch("oase.commands.X.requests")``), never by
swapping the package in ``sys.modules``.
"""

from __future__ import annotations

import sys
import types

import click
import pytest
import requests
import yaml

_CORE_MODULES: dict[str, types.ModuleType] = {
    "click": click,
    "requests": requests,
    "yaml": yaml,
}


def _leaked_core_module(when: str) -> str | None:
    """Return an error message if a core package is not the real module."""
    for name, real in _CORE_MODULES.items():
        current = sys.modules.get(name)
        if current is not real:
            return (
                f"sys.modules[{name!r}] is {current!r} {when}, not the real "
                f"{name} package. A test module replaced a core dependency "
                "in sys.modules without restoring it. Patch it where it is "
                "used instead."
            )
    return None


def pytest_collection_finish(session: pytest.Session) -> None:
    """Stop the run if a test module leaked a core-dependency mock at import."""
    message = _leaked_core_module("after test collection")
    if message:
        pytest.exit(message, returncode=pytest.ExitCode.TESTS_FAILED)


@pytest.fixture(autouse=True)
def _core_modules_are_real():
    """Check before and after each test that core packages are not mocked."""
    message = _leaked_core_module("before this test")
    if message:
        pytest.fail(message, pytrace=False)
    yield
    message = _leaked_core_module("after this test")
    if message:
        pytest.fail(message, pytrace=False)
