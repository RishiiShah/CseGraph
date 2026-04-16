"""Unit tests for the etl_pipeline_oop sandbox."""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "sandboxes", "etl_pipeline_oop"),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from stages.transform import clean_text, parse_score, NormalizeStage
from stages.loader import CSVLoader
from stages.export import JsonWriter
from core.pipeline import Pipeline


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------

def test_clean_text_strips_whitespace():
    assert clean_text("  hello  ") == "hello"


def test_clean_text_lowercases():
    assert clean_text("UPPER") == "upper"


def test_clean_text_both():
    assert clean_text("  Hello World  ") == "hello world"


# ---------------------------------------------------------------------------
# parse_score
# ---------------------------------------------------------------------------

def test_parse_score_valid_integer():
    assert parse_score("85") == 85


def test_parse_score_zero():
    assert parse_score("0") == 0


def test_parse_score_invalid_string():
    assert parse_score("abc") == 0


def test_parse_score_empty_string():
    assert parse_score("") == 0


# ---------------------------------------------------------------------------
# NormalizeStage
# ---------------------------------------------------------------------------

def test_normalize_stage_name_and_score():
    stage = NormalizeStage()
    rows = [{"id": "1", "name": " Alice ", "score": "75"}]
    result = stage.transform(rows)
    assert result[0]["name"] == "alice"
    assert result[0]["score"] == 75


def test_normalize_stage_passing_threshold():
    stage = NormalizeStage()
    rows = [{"id": "1", "name": "Alice", "score": "60"}]
    result = stage.transform(rows)
    assert result[0]["is_passing"] is True


def test_normalize_stage_failing_below_threshold():
    stage = NormalizeStage()
    rows = [{"id": "2", "name": "Bob", "score": "59"}]
    result = stage.transform(rows)
    assert result[0]["is_passing"] is False


def test_normalize_stage_preserves_id():
    stage = NormalizeStage()
    rows = [{"id": "uid-99", "name": "Carol", "score": "80"}]
    result = stage.transform(rows)
    assert result[0]["id"] == "uid-99"


def test_normalize_stage_multiple_rows():
    stage = NormalizeStage()
    rows = [
        {"id": "1", "name": "Alice", "score": "90"},
        {"id": "2", "name": "Bob", "score": "40"},
    ]
    result = stage.transform(rows)
    assert len(result) == 2
    assert result[0]["is_passing"] is True
    assert result[1]["is_passing"] is False


# ---------------------------------------------------------------------------
# CSVLoader
# ---------------------------------------------------------------------------

def test_csv_loader_parses_row():
    loader = CSVLoader()
    result = loader.load(["u1, Alice, 90"])
    assert len(result) == 1
    assert result[0]["id"] == "u1"
    assert result[0]["name"] == "Alice"
    assert result[0]["score"] == "90"


def test_csv_loader_multiple_rows():
    loader = CSVLoader()
    result = loader.load(["u1, Alice, 90", "u2, Bob, 55"])
    assert len(result) == 2


def test_csv_loader_skips_malformed_rows():
    loader = CSVLoader()
    result = loader.load(["bad_row", "u1, Alice, 90"])
    assert len(result) == 1


def test_csv_loader_empty_input():
    loader = CSVLoader()
    assert loader.load([]) == []


# ---------------------------------------------------------------------------
# JsonWriter
# ---------------------------------------------------------------------------

def test_json_writer_summary_total():
    writer = JsonWriter()
    rows = [
        {"id": "1", "name": "alice", "score": 90, "is_passing": True},
        {"id": "2", "name": "bob", "score": 50, "is_passing": False},
    ]
    result = writer.write(rows)
    assert result[0]["summary"]["total"] == 2


def test_json_writer_summary_passing_count():
    writer = JsonWriter()
    rows = [
        {"id": "1", "name": "alice", "score": 90, "is_passing": True},
        {"id": "2", "name": "bob", "score": 50, "is_passing": False},
    ]
    result = writer.write(rows)
    assert result[0]["summary"]["passing"] == 1


def test_json_writer_avg_score():
    writer = JsonWriter()
    rows = [
        {"id": "1", "name": "alice", "score": 90, "is_passing": True},
        {"id": "2", "name": "bob", "score": 50, "is_passing": False},
    ]
    result = writer.write(rows)
    assert result[0]["summary"]["avg_score"] == 70.0


def test_json_writer_records_included():
    writer = JsonWriter()
    rows = [{"id": "1", "name": "alice", "score": 90, "is_passing": True}]
    result = writer.write(rows)
    assert result[0]["records"] == rows


def test_json_writer_empty_rows():
    writer = JsonWriter()
    result = writer.write([])
    assert result[0]["summary"]["total"] == 0
    assert result[0]["summary"]["avg_score"] == 0


# ---------------------------------------------------------------------------
# Pipeline (end-to-end)
# ---------------------------------------------------------------------------

def test_pipeline_run_end_to_end():
    pipeline = Pipeline(CSVLoader(), NormalizeStage(), JsonWriter())
    raw = ["u1, Alice, 90", "u2, Bob, 50"]
    result = pipeline.run(raw)
    assert result[0]["summary"]["total"] == 2


def test_pipeline_run_passing_count():
    pipeline = Pipeline(CSVLoader(), NormalizeStage(), JsonWriter())
    raw = ["u1, Alice, 90", "u2, Bob, 50", "u3, Carol, 65"]
    result = pipeline.run(raw)
    assert result[0]["summary"]["passing"] == 2


def test_pipeline_skips_malformed_csv():
    pipeline = Pipeline(CSVLoader(), NormalizeStage(), JsonWriter())
    raw = ["bad", "u1, Alice, 80"]
    result = pipeline.run(raw)
    assert result[0]["summary"]["total"] == 1
