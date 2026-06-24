"""Integration tests for csegraph watch module."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch


class TestWatchImport:
    def test_module_importable(self):
        from csegraph._core.watch import watch

        assert callable(watch)


class TestSupportedExtensions:
    def test_registry_has_py(self):
        from csegraph._core.languages.registry import registry

        exts = registry.supported_extensions()
        assert ".py" in exts

    def test_returns_set(self):
        from csegraph._core.languages.registry import registry

        exts = registry.supported_extensions()
        assert isinstance(exts, set)


class TestWatchKeyboardInterrupt:
    def test_ctrl_c_exits_gracefully(self, tmp_path: Path, caplog):
        from csegraph._core.watch import watch

        def _raise_interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt

        with caplog.at_level(logging.INFO, logger="csegraph._core.watch"):
            with patch("watchfiles.watch", side_effect=_raise_interrupt):
                watch(str(tmp_path), str(tmp_path / ".csegraph" / "index.db"))

        log_text = caplog.text
        assert "Stopped watching." in log_text
        assert "KeyboardInterrupt" not in log_text
        assert "Traceback" not in log_text


def test_watch_suppresses_watchfiles_info_logs_by_default(tmp_path: Path):
    from csegraph._core.watch import watch

    watchfiles_logger = logging.getLogger("watchfiles")
    watchfiles_main_logger = logging.getLogger("watchfiles.main")
    old_watchfiles_level = watchfiles_logger.level
    old_watchfiles_main_level = watchfiles_main_logger.level

    def _raise_interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    try:
        watchfiles_logger.setLevel(logging.NOTSET)
        watchfiles_main_logger.setLevel(logging.NOTSET)
        with patch("watchfiles.watch", side_effect=_raise_interrupt):
            watch(str(tmp_path), str(tmp_path / ".csegraph" / "index.db"))

        assert watchfiles_logger.level == logging.WARNING
        assert watchfiles_main_logger.level == logging.WARNING
    finally:
        watchfiles_logger.setLevel(old_watchfiles_level)
        watchfiles_main_logger.setLevel(old_watchfiles_main_level)
