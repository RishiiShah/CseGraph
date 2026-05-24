"""Integration tests for RegistryService — multi-repo registry."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from csegraph_core.registry import RegistryService
from csegraph_core.core.models import to_dict


class TestRegistryRegister:
    def test_register_new_repo(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        result = svc.register(repo)

        assert result.action == "registered"
        assert result.alias == "my-app"
        assert len(result.entries) == 1
        assert result.entries[0].root == str(repo.resolve())
        assert result.entries[0].profile == "medium"
        assert reg_file.exists()

    def test_register_with_custom_alias(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        result = svc.register(repo, alias="app")

        assert result.alias == "app"
        assert result.entries[0].alias == "app"

    def test_register_with_profile(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        result = svc.register(repo, profile="large")

        assert result.entries[0].profile == "large"

    def test_register_with_custom_db(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()
        custom_db = tmp_path / "custom.db"

        svc = RegistryService(reg_file)
        result = svc.register(repo, db=str(custom_db))

        assert result.entries[0].db == str(custom_db.resolve())

    def test_register_updates_existing(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        first = svc.register(repo, profile="small")
        assert first.action == "registered"

        second = svc.register(repo, profile="large")
        assert second.action == "updated"
        assert second.entries[0].profile == "large"
        assert second.entries[0].added_at == first.entries[0].added_at

    def test_register_nonexistent_dir_raises(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        svc = RegistryService(reg_file)

        with pytest.raises(ValueError, match="does not exist"):
            svc.register(tmp_path / "nonexistent")

    def test_register_default_db_path(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        result = svc.register(repo)

        expected_db = str(repo.resolve() / ".csegraph" / "index.db")
        assert result.entries[0].db == expected_db


class TestRegistryUnregister:
    def test_unregister_existing(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        svc.register(repo, alias="app")
        result = svc.unregister("app")

        assert result.action == "unregistered"
        assert result.alias == "app"
        assert svc.list().entries == []

    def test_unregister_unknown_raises(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        svc = RegistryService(reg_file)

        with pytest.raises(ValueError, match="No registered repo"):
            svc.unregister("nope")


class TestRegistryList:
    def test_list_empty(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        svc = RegistryService(reg_file)
        result = svc.list()

        assert result.action == "list"
        assert result.entries == []
        assert "0" in result.message

    def test_list_multiple(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        for name in ("alpha", "beta", "gamma"):
            (tmp_path / name).mkdir()

        svc = RegistryService(reg_file)
        for name in ("alpha", "beta", "gamma"):
            svc.register(tmp_path / name)

        result = svc.list()
        assert len(result.entries) == 3
        aliases = [e.alias for e in result.entries]
        assert aliases == sorted(aliases)

    def test_list_preserves_data_across_operations(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()

        svc = RegistryService(reg_file)
        svc.register(tmp_path / "a")
        svc.register(tmp_path / "b")
        svc.unregister("a")

        result = svc.list()
        assert len(result.entries) == 1
        assert result.entries[0].alias == "b"


class TestRegistryGet:
    def test_get_existing(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        svc.register(repo, alias="app", profile="large")
        entry = svc.get("app")

        assert entry.alias == "app"
        assert entry.root == str(repo.resolve())
        assert entry.profile == "large"

    def test_get_unknown_raises(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        svc = RegistryService(reg_file)

        with pytest.raises(ValueError, match="No registered repo"):
            svc.get("nope")


class TestRegistrySerialization:
    def test_registry_result_serializes(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        svc = RegistryService(reg_file)
        result = svc.register(repo)
        payload = to_dict(result)

        assert payload["command"] == "registry"
        assert payload["action"] == "registered"
        assert len(payload["entries"]) == 1
        assert payload["entries"][0]["alias"] == "my-app"
        json.dumps(payload)


class TestRegistryPersistence:
    def test_survives_service_recreate(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        RegistryService(reg_file).register(repo, alias="app")

        svc2 = RegistryService(reg_file)
        result = svc2.list()
        assert len(result.entries) == 1
        assert result.entries[0].alias == "app"

    def test_registry_json_is_valid(self, tmp_path: Path):
        reg_file = tmp_path / "registry.json"
        repo = tmp_path / "my-app"
        repo.mkdir()

        RegistryService(reg_file).register(repo)

        data = json.loads(reg_file.read_text(encoding="utf-8"))
        assert "repos" in data
        assert "my-app" in data["repos"]
