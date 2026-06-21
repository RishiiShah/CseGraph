"""Node IDs are opaque. Treat as opaque strings; do not parse. Use core.models fields for structural access. Format is internal."""

from __future__ import annotations


def file_node_id(rel_path: str) -> str:
    return f"file::{rel_path}"


def symbol_node_id(rel_path: str, kind: str, name: str) -> str:
    return f"symbol::{rel_path}::{kind}::{name}"


def folder_node_id(rel_dir: str) -> str:
    return f"folder::{rel_dir}"


def repo_node_id(name: str) -> str:
    return f"repo::{name}"
