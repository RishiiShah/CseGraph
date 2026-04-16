"""ml_training_pipeline — demo entry point."""
from data.loader import InMemoryLoader
from data.preprocessor import preprocess
from data.splitter import TrainTestSplitter
from evaluation.evaluator import ModelEvaluator
from evaluation.reporter import EvaluationReport, format_report
from ml_models.ensemble import VotingEnsemble
from ml_models.linear import LinearRegressor
from ml_models.tree import DecisionTreeClassifier
from training.contracts import TrainingConfig
from training.trainer import ModelTrainer


def run_demo() -> dict:
    records = [
        {"x1": float(i), "x2": float(i % 3), "label": float(i % 2)}
        for i in range(20)
    ]
    features = ["x1", "x2"]
    processed = preprocess(records, features)

    splitter = TrainTestSplitter(test_ratio=0.2)
    split = splitter.split(records, features)

    X_train = [[r["x1"], r["x2"]] for r in processed[: len(split.train)]]
    y_train = [r["label"] for r in records[: len(split.train)]]
    X_test = [[r["x1"], r["x2"]] for r in processed[len(split.train) :]]
    y_test = [r["label"] for r in records[len(split.train) :]]

    lr = LinearRegressor(lr=0.1, epochs=50)
    dt = DecisionTreeClassifier(max_depth=2)
    ensemble = VotingEnsemble([lr, dt])

    config = TrainingConfig(learning_rate=0.1, epochs=50)
    trainer = ModelTrainer(ensemble, config)
    train_result = trainer.train(X_train, y_train)

    evaluator = ModelEvaluator()
    results = evaluator.evaluate(ensemble, X_test, y_test)

    report = EvaluationReport("ml_training_pipeline")
    report.add_results(results)
    summary = report.summary()
    summary["training"] = train_result
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_demo(), indent=2))
