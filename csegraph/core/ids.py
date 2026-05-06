from __future__ import annotations


def file_node_id(rel_path: str) -> str:
    return f"file::{rel_path}"


def symbol_node_id(rel_path: str, kind: str, name: str) -> str:
    return f"symbol::{rel_path}::{kind}::{name}"
