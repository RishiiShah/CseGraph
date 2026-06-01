"""Integration tests for DaemonService — multi-repo watch manager."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from csegraph_core.daemon import DaemonService, _is_alive
from csegraph_core.registry import RegistryService
from csegraph_core.core.models import to_dict


def _setup_registry(tmp_path: Path, names: list[str]) -> Path:
    reg_file = tmp_path / "registry.json"
    svc = RegistryService(reg_file)
    for name in names:
        repo = tmp_path / name
        repo.mkdir(exist_ok=True)
        svc.register(repo)
    return reg_file


class TestDaemonStatus:
    def test_status_empty_registry(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        result = svc.status()
        assert result.running is False
        assert result.entries == []
        assert "0/0" in result.message

    def test_status_no_watchers_running(self, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["app-a", "app-b"])
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        result = svc.status()
        assert result.running is False
        assert len(result.entries) == 2
        assert all(e.status == "stopped" for e in result.entries)

    def test_status_with_stale_pid_file(self, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"
        pids_dir.mkdir()
        (pids_dir / "my-app.pid").write_text("999999", encoding="utf-8")

        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
        result = svc.status()

        assert result.entries[0].status == "stopped"
        assert not (pids_dir / "my-app.pid").exists()


class TestDaemonStart:
    def test_start_no_repos_registered(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        result = svc.start()
        assert result.running is False
        assert "No repos registered" in result.message

    def test_start_unknown_alias_raises(self, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["app-a"])
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        with pytest.raises(ValueError, match="Unknown aliases"):
            svc.start(aliases=["nonexistent"])

    @patch("csegraph_core.daemon.subprocess.Popen")
    def test_start_spawns_process(self, mock_popen, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
        result = svc.start()

        assert result.running is True
        assert len(result.entries) == 1
        assert result.entries[0].status == "started"
        assert result.entries[0].pid == 12345
        assert (pids_dir / "my-app.pid").exists()

    @patch("csegraph_core.daemon.subprocess.Popen")
    def test_start_skips_already_running(self, mock_popen, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"
        pids_dir.mkdir()

        existing_pid = 54321
        (pids_dir / "my-app.pid").write_text(str(existing_pid), encoding="utf-8")

        with patch("csegraph_core.daemon._is_alive", return_value=True):
            svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
            result = svc.start()

        mock_popen.assert_not_called()
        assert result.entries[0].status == "already_running"
        assert result.entries[0].pid == existing_pid

    @patch("csegraph_core.daemon.subprocess.Popen")
    def test_start_with_profile_override(self, mock_popen, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"

        mock_proc = MagicMock()
        mock_proc.pid = 11111
        mock_popen.return_value = mock_proc

        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
        svc.start(profile="large")

        call_args = mock_popen.call_args[0][0]
        assert "--profile" in call_args
        idx = call_args.index("--profile")
        assert call_args[idx + 1] == "large"

    @patch("csegraph_core.daemon.subprocess.Popen")
    def test_start_specific_aliases(self, mock_popen, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["app-a", "app-b", "app-c"])
        pids_dir = tmp_path / "pids"

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_popen.return_value = mock_proc

        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
        result = svc.start(aliases=["app-b"])

        assert len(result.entries) == 1
        assert result.entries[0].alias == "app-b"


class TestDaemonStop:
    def test_stop_no_watchers(self, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        result = svc.stop()
        assert all(e.status == "not_running" for e in result.entries)

    @patch("csegraph_core.daemon._kill_process", return_value=True)
    @patch("csegraph_core.daemon._is_alive", return_value=True)
    def test_stop_kills_running(self, mock_alive, mock_kill, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"
        pids_dir.mkdir()
        (pids_dir / "my-app.pid").write_text("12345", encoding="utf-8")

        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
        result = svc.stop()

        assert result.entries[0].status == "stopped"
        assert result.entries[0].pid == 12345
        mock_kill.assert_called_once_with(12345)
        assert not (pids_dir / "my-app.pid").exists()

    def test_stop_specific_alias(self, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["app-a", "app-b"])
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        result = svc.stop(aliases=["app-a"])
        assert len(result.entries) == 1
        assert result.entries[0].alias == "app-a"


class TestDaemonSerialization:
    @patch("csegraph_core.daemon.subprocess.Popen")
    def test_daemon_result_serializes(self, mock_popen, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)
        result = svc.start()
        payload = to_dict(result)

        assert payload["command"] == "daemon"
        assert payload["running"] is True
        assert len(payload["entries"]) == 1
        assert payload["entries"][0]["pid"] == 42
        json.dumps(payload)

    def test_status_result_serializes(self, tmp_path: Path):
        reg_file = _setup_registry(tmp_path, ["my-app"])
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        result = svc.status()
        payload = to_dict(result)

        assert payload["command"] == "daemon"
        assert isinstance(payload["entries"], list)
        json.dumps(payload)


class TestDaemonPidManagement:
    def test_write_and_read_pid(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        svc._write_pid("test", 12345)
        assert svc._read_pid("test") == 12345

    def test_read_missing_pid(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        assert svc._read_pid("nope") is None

    def test_remove_pid(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        pids_dir = tmp_path / "pids"
        svc = DaemonService(registry_path=reg_file, pids_dir=pids_dir)

        svc._write_pid("test", 12345)
        svc._remove_pid("test")
        assert svc._read_pid("test") is None
