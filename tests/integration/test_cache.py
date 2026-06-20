import sqlite3
from collections import defaultdict

from csegraph._core.index.loaders import load_edge_maps, load_symbols
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval import minimal as minimal_module
from csegraph._core.retrieval.cache import CACHE, SnapshotManager
from csegraph._core.retrieval.scoring import apply_graph_expansion, apply_graph_expansion_from_maps
from csegraph._core.server.app import _dispatch_tool


def test_cache_initializes_empty():
    manager = SnapshotManager()
    assert len(manager._snapshots) == 0


def test_cache_hits_after_first_call(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".csegraph" / "index.db"
    IndexService(db_path).index(str(repo), profile="small")

    manager = SnapshotManager()
    index = ProjectIndex(db_path)
    index.initialize_schema()
    
    # First call: miss, loads snapshot
    snap1 = manager.get_snapshot(index)
    
    # Second call: hit, returns same snapshot object
    snap2 = manager.get_snapshot(index)
    
    assert snap1 is snap2
    assert manager._snapshots[str(db_path)] is snap1
    index.close()


def test_cache_evicts_lru(tmp_path):
    manager = SnapshotManager(max_size=2)
    
    for i in range(3):
        db_path = tmp_path / f"db_{i}.db"
        index = ProjectIndex(db_path)
        index.initialize_schema()
        manager.get_snapshot(index)
        index.close()
        
    assert len(manager._snapshots) == 2
    # The first one should have been evicted
    assert str(tmp_path / "db_0.db") not in manager._snapshots


def test_cache_invalidation_by_data_version(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".csegraph" / "index.db"
    IndexService(db_path).index(str(repo), profile="small")

    manager = SnapshotManager()
    index = ProjectIndex(db_path)
    index.initialize_schema()
    
    snap1 = manager.get_snapshot(index)
    
    # Any write to the DB updates the file mtime. Sleep to ensure mtime resolution boundary is crossed.
    import time
    time.sleep(0.1)
    conn2 = sqlite3.connect(db_path)
    conn2.execute("INSERT INTO nodes (id, type, name, path, language, source_hash, updated_at) VALUES ('test_node', 'file', 'test.py', 'test.py', '', 'hash', 0)")
    conn2.commit()
    conn2.close()
    
    snap2 = manager.get_snapshot(index)
    
    assert snap1 is not snap2
    assert snap1.data_version != snap2.data_version
    index.close()


def test_cache_hooks_in_app(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".csegraph" / "index.db"
    
    # Run index via app
    args = {"repo": str(repo), "profile": "small", "postprocess_level": "none"}
    minimal_module._hub_cache[("dummy", 1)] = (50, frozenset({"hub"}))
    res = _dispatch_tool("csegraph_index", args)
    assert "files_indexed" in res
    
    # Cache should be cleared by the index hook
    assert str(db_path) not in CACHE._snapshots
    assert not minimal_module._hub_cache
    
    # Now let's cache something
    index = ProjectIndex(db_path)
    index.initialize_schema()
    CACHE.get_snapshot(index)
    assert str(db_path) in CACHE._snapshots
    index.close()
    
    # Run refresh via app
    args_refresh = {"repo": str(repo), "profile": "small", "postprocess_level": "none"}
    minimal_module._hub_cache[("dummy", 2)] = (50, frozenset({"hub"}))
    _dispatch_tool("csegraph_refresh", args_refresh)
    
    # Cache should be cleared by the refresh hook
    assert str(db_path) not in CACHE._snapshots
    assert not minimal_module._hub_cache


def test_cached_graph_expansion_matches_sql_cte(tmp_path):
    db_path = tmp_path / "index.db"
    index = ProjectIndex(db_path)
    index.initialize_schema()
    try:
        rows = [
            ("symbol::a.py::function::a", "function", "a", "a.py"),
            ("file::bridge.py", "file", "bridge.py", "bridge.py"),
            ("symbol::b.py::function::b", "function", "b", "b.py"),
            ("symbol::c.py::function::c", "function", "c", "c.py"),
        ]
        for node_id, kind, name, path in rows:
            index.conn.execute(
                """
                INSERT INTO nodes
                  (id, type, name, path, language, source_hash, updated_at)
                VALUES (?, ?, ?, ?, 'python', 'hash', 0)
                """,
                (node_id, kind, name, path),
            )
        index.conn.execute(
            """
            INSERT INTO edges (source, target, relation, confidence, confidence_tier)
            VALUES (?, ?, 'contains', 1.0, 'EXTRACTED')
            """,
            ("symbol::a.py::function::a", "file::bridge.py"),
        )
        index.conn.execute(
            """
            INSERT INTO edges (source, target, relation, confidence, confidence_tier)
            VALUES (?, ?, 'calls', 1.0, 'EXTRACTED')
            """,
            ("file::bridge.py", "symbol::b.py::function::b"),
        )
        index.conn.execute(
            """
            INSERT INTO edges (source, target, relation, confidence, confidence_tier)
            VALUES (?, ?, 'tested_by', 1.0, 'EXTRACTED')
            """,
            ("symbol::c.py::function::c", "symbol::a.py::function::a"),
        )
        index.conn.commit()

        symbols = load_symbols(index, exclude_heavy=True)
        outgoing, incoming = load_edge_maps(index)
        sql_scores = defaultdict(float)
        sql_evidence = defaultdict(list)
        cached_scores = defaultdict(float)
        cached_evidence = defaultdict(list)

        apply_graph_expansion(
            "symbol::a.py::function::a",
            2,
            sql_scores,
            sql_evidence,
            index.conn,
            symbols,
        )
        apply_graph_expansion_from_maps(
            "symbol::a.py::function::a",
            2,
            cached_scores,
            cached_evidence,
            outgoing,
            incoming,
            symbols,
        )

        assert dict(cached_scores) == dict(sql_scores)
        assert {
            node_id: sorted(items)
            for node_id, items in cached_evidence.items()
        } == {node_id: sorted(items) for node_id, items in sql_evidence.items()}
    finally:
        index.close()
