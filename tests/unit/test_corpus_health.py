import sqlite3
import time

from csegraph._core.corpus_health import assess_index_health, collect_index_metrics


def _empty_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, type TEXT, path TEXT, language TEXT,
            start_line INTEGER, end_line INTEGER, parse_status TEXT, updated_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE edges (source TEXT, target TEXT, relation TEXT)
        """
    )
    conn.execute("CREATE TABLE lexical_index (node_id TEXT)")
    return conn


def test_thin_index_verdict():
    health = assess_index_health({"files": 1, "symbols": 2, "edges": 0, "approx_loc": 50})
    assert health.verdict == "thin"
    assert "small" in health.summary.lower() or "little" in health.summary.lower()


def test_large_index_verdict():
    health = assess_index_health(
        {"files": 600, "symbols": 9000, "edges": 10000, "approx_loc": 50000}
    )
    assert health.verdict == "large"
    assert (
        "target" in " ".join(health.hints).lower()
        or "detail_level" in " ".join(health.hints).lower()
    )


def test_age_only_index_verdict_is_cautious():
    health = assess_index_health(
        {"files": 20, "symbols": 100, "edges": 200, "approx_loc": 2000},
        index_age_hours=30.0,
    )
    assert health.verdict == "aged"
    assert "age-check cautious" in health.summary


def test_stale_index_verdict_from_head_warning():
    health = assess_index_health(
        {"files": 20, "symbols": 100, "edges": 200, "approx_loc": 2000},
        index_age_hours=30.0,
        external_warnings=["Graph was built at commit old but HEAD is now new."],
    )
    assert health.verdict == "stale"


def test_rebuild_from_schema_warning():
    health = assess_index_health(
        {"files": 10, "symbols": 50, "edges": 80, "approx_loc": 1000},
        external_warnings=["Schema mismatch: rebuild required."],
    )
    assert health.verdict == "rebuild"


def test_collect_index_metrics_counts(tmp_path):
    conn = _empty_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO nodes VALUES ('f1', 'file', 'a.py', 'python', NULL, NULL, 'ok', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO nodes VALUES ('s1', 'function', 'a.py', 'python', 1, 10, 'ok', ?)",
        (now,),
    )
    conn.execute("INSERT INTO edges VALUES ('s1', 'f1', 'contains')")
    conn.execute("INSERT INTO lexical_index VALUES ('s1')")
    metrics = collect_index_metrics(conn)
    assert metrics["files"] == 1
    assert metrics["symbols"] == 1
    assert metrics["approx_loc"] == 10
    assert metrics["fts_entries"] == 1
    conn.close()
