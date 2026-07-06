from __future__ import annotations

ADAPTIVE_SCHEMA_VERSION = "csegraph-context-v5"
ADAPTIVE_ENGINE_VERSION = "adaptive-v2"
MAX_CANDIDATES = 64
MAX_SLICES = 5
TARGET_CONFIDENCE_THRESHOLD = 0.75
TARGET_MARGIN_THRESHOLD = 0.15
_IMPACT_RELATIONS = {
    "calls",
    "decorates",
    "dispatches",
    "imports",
    "inherits",
    "registers",
    "tested_by",
}
_GENERATED_PATH_PARTS = {
    ".generated",
    ".venv",
    "build",
    "dist",
    "generated",
    "node_modules",
    "third_party",
    "vendor",
}
_EDIT_WORDS = {
    "add",
    "change",
    "edit",
    "fix",
    "implement",
    "migrate",
    "modify",
    "refactor",
    "remove",
    "rename",
    "replace",
    "update",
}
_DEBUG_WORDS = {
    "bug",
    "crash",
    "debug",
    "error",
    "exception",
    "failed",
    "failing",
    "failure",
    "regression",
    "traceback",
}
_TEST_WORDS = {"assert", "coverage", "pytest", "test", "tests"}
_STRUCTURAL_WORDS = {
    "architecture",
    "blast",
    "callers",
    "dependency",
    "dependencies",
    "graph",
    "impact",
    "path",
    "structure",
}
