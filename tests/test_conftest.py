"""The loop-factory hook in `conftest.py` must be valid on every platform the suite runs on.

pytest-asyncio rejects a `pytest_asyncio_loop_factories` hook that returns anything but a
non-empty mapping, and fails collection of every test when it does. Returning `None` for
"keep the default loop" is that failure, so the hook exists only where it has a mapping.
"""

import asyncio
import sys

import conftest
from conftest import loop_factories


def test_windows_gets_the_selector_loop():
    assert loop_factories("win32") == {"selector": asyncio.SelectorEventLoop}


def test_other_platforms_keep_the_default_loop():
    assert loop_factories("linux") is None
    assert loop_factories("darwin") is None


def test_hook_is_registered_only_with_a_mapping_to_return():
    hook = getattr(conftest, "pytest_asyncio_loop_factories", None)
    if loop_factories(sys.platform) is None:
        assert hook is None
        return
    factories = hook(config=None, item=None)
    assert factories
    assert all(isinstance(name, str) and name for name in factories)
    assert all(callable(factory) for factory in factories.values())
