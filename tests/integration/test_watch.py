"""Integration tests for csegraph watch module."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestWatchImport:
    def test_module_importable(self):
        from csegraph_core.watch import watch
        assert callable(watch)


class TestSupportedExtensions:
    def test_registry_has_py(self):
        from csegraph_core.languages.registry import registry
        exts = registry.supported_extensions()
        assert ".py" in exts

    def test_returns_set(self):
        from csegraph_core.languages.registry import registry
        exts = registry.supported_extensions()
        assert isinstance(exts, set)
