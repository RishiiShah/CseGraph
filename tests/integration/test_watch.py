"""Integration tests for csegraph watch module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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


class TestWatchKeyboardInterrupt:
    def test_ctrl_c_exits_gracefully(self, tmp_path: Path, capsys):
        from csegraph_core.watch import watch

        def _raise_interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt

        with patch("watchfiles.watch", side_effect=_raise_interrupt):
            watch(str(tmp_path), str(tmp_path / ".csegraph" / "index.db"))

        captured = capsys.readouterr()
        assert "Stopped watching." in captured.err
        assert "KeyboardInterrupt" not in captured.err
        assert "Traceback" not in captured.err
