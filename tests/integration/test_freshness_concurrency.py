import concurrent.futures
import time
from pathlib import Path
from unittest.mock import patch

from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService


def test_atomic_write_concurrent_readers(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")

    db_path = tmp_path / "index.db"
    IndexService(db_path).index(repo)

    # Readers should see revision 1
    index = ProjectIndex(db_path)
    assert index.index_revision() == 1
    nodes = index.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
    assert nodes > 0
    index.close()

    # We will mock _write_parsed_files to delay slightly
    # and we will read concurrently to ensure no reader sees an empty graph (0 nodes)
    read_results = []

    def reader_task():
        for _ in range(10):
            idx = ProjectIndex(db_path)
            try:
                count = idx.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
                read_results.append(count)
            finally:
                idx.close()
            time.sleep(0.01)

    from csegraph._core.index.services import _write_parsed_files as real_write

    with patch("csegraph._core.index.services._write_parsed_files") as mock_write:

        def delayed_write(idx, root, files):
            time.sleep(0.05)
            return real_write(idx, root, files)

        mock_write.side_effect = delayed_write

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(IndexService(db_path).index, repo)
            reader_future = executor.submit(reader_task)

            writer_future.result()
            reader_future.result()

    # The reader should never see an empty graph (count == 0)
    # It should see either the old count or the new count (both > 0)
    for res in read_results:
        assert res > 0, "Reader saw an empty graph during atomic write!"


def test_atomic_write_rollback_on_failure(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")

    db_path = tmp_path / "index.db"
    IndexService(db_path).index(repo)

    index = ProjectIndex(db_path)
    original_revision = index.index_revision()
    original_nodes = index.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
    index.close()

    # Inject a failure during writing
    with patch(
        "csegraph._core.index.services._write_parsed_files", side_effect=Exception("Disk Error")
    ):
        try:
            IndexService(db_path).index(repo)
        except Exception:
            pass

    # The graph should be rolled back perfectly
    index = ProjectIndex(db_path)
    assert index.index_revision() == original_revision
    nodes = index.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
    assert nodes == original_nodes
    index.close()
