from core.pipeline import Pipeline
from stages.export import JsonWriter
from stages.loader import CSVLoader
from stages.transform import NormalizeStage


def run_pipeline() -> list[dict]:
    pipeline = Pipeline(
        loader=CSVLoader(),
        transformer=NormalizeStage(),
        writer=JsonWriter(),
    )
    return pipeline.run([
        "u-1, Alice , 88",
        "u-2,BOB,54",
        "u-3, Carol, 71",
    ])
