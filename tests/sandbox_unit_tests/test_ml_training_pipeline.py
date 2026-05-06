"""Unit tests for the ml_training_pipeline sandbox.

SANDBOX_PATH env var lets the runner substitute a generated file.
"""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(
        os.path.dirname(__file__), "..", "..", "sandboxes", "ml_training_pipeline"
    ),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from data.contracts import DataSplit
from data.loader import CSVLoader, InMemoryLoader, load_csv, load_memory
from data.preprocessor import (
    DataPreprocessor,
    FeatureScaler,
    Normalizer,
    normalize,
    preprocess,
    scale,
)
from data.splitter import (
    KFoldSplitter,
    TrainTestSplitter,
    k_fold_split,
    split,
)
from evaluation.contracts import EvalResult
from evaluation.evaluator import ModelEvaluator, aggregate_metrics, evaluate
from evaluation.metrics import (
    MAE,
    MSE,
    Accuracy,
    compute_accuracy,
    compute_mse,
)
from evaluation.reporter import EvaluationReport, format_report, summarize
from ml_models.ensemble import EnsembleModel, VotingEnsemble, predict_ensemble
from ml_models.linear import LinearRegressor, fit_linear, predict_linear
from ml_models.tree import DecisionNode, DecisionTreeClassifier, fit_tree
from training.callbacks import CheckpointSaver, EarlyStopping, LearningRateScheduler
from training.contracts import EpochLog, TrainingConfig
from training.optimizer import AdamOptimizer, SGDOptimizer
from training.trainer import ModelTrainer, evaluate_step, train


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RECORDS = [{"x": float(i), "y": float(i * 2)} for i in range(10)]
_FEATURES = ["x", "y"]


# ---------------------------------------------------------------------------
# data.loader
# ---------------------------------------------------------------------------


class TestInMemoryLoader:
    def test_load_returns_copy(self):
        records = [{"a": 1}]
        loader = InMemoryLoader(records)
        loaded = loader.load()
        assert loaded == records
        assert loaded is not records

    def test_load_memory_function(self):
        records = [{"z": 99}]
        assert load_memory(records) == records


class TestCSVLoader:
    def test_parses_csv_string(self):
        content = "name,score\nalice,90\nbob,80\n"
        rows = CSVLoader(content).load()
        assert len(rows) == 2
        assert rows[0]["name"] == "alice"
        assert rows[1]["score"] == "80"

    def test_load_csv_function(self):
        content = "a,b\n1,2\n"
        assert load_csv(content) == [{"a": "1", "b": "2"}]


# ---------------------------------------------------------------------------
# data.preprocessor
# ---------------------------------------------------------------------------


class TestNormalizer:
    def test_transforms_to_zero_one_range(self):
        records = [{"x": 0.0}, {"x": 10.0}]
        n = Normalizer()
        n.fit(records, ["x"])
        assert n.transform({"x": 0.0})["x"] == 0.0
        assert n.transform({"x": 10.0})["x"] == 1.0

    def test_midpoint_is_half(self):
        records = [{"x": 0.0}, {"x": 10.0}]
        n = Normalizer()
        n.fit(records, ["x"])
        assert abs(n.transform({"x": 5.0})["x"] - 0.5) < 1e-9

    def test_constant_feature_no_division_error(self):
        records = [{"x": 3.0}, {"x": 3.0}]
        n = Normalizer()
        n.fit(records, ["x"])
        result = n.transform({"x": 3.0})
        assert result["x"] == 0.0


class TestFeatureScaler:
    def test_zero_mean_after_fit(self):
        records = [{"v": 1.0}, {"v": 3.0}, {"v": 5.0}]
        s = FeatureScaler()
        s.fit(records, ["v"])
        transformed = [s.transform(r) for r in records]
        mean = sum(r["v"] for r in transformed) / len(transformed)
        assert abs(mean) < 1e-9


class TestPreprocessFunctions:
    def test_normalize_returns_same_length(self):
        result = normalize(_RECORDS, _FEATURES)
        assert len(result) == len(_RECORDS)

    def test_scale_returns_same_length(self):
        result = scale(_RECORDS, _FEATURES)
        assert len(result) == len(_RECORDS)

    def test_preprocess_returns_same_length(self):
        result = preprocess(_RECORDS, _FEATURES)
        assert len(result) == len(_RECORDS)


class TestDataPreprocessor:
    def test_fit_transform_with_scaler(self):
        dp = DataPreprocessor(scaler=FeatureScaler())
        result = dp.fit_transform(_RECORDS, _FEATURES)
        assert len(result) == len(_RECORDS)


# ---------------------------------------------------------------------------
# data.splitter
# ---------------------------------------------------------------------------


class TestTrainTestSplitter:
    def test_split_ratio(self):
        records = list(range(10))
        records = [{"i": float(i)} for i in range(10)]
        s = TrainTestSplitter(test_ratio=0.2)
        result = s.split(records)
        assert len(result.train) == 8
        assert len(result.test) == 2

    def test_split_function(self):
        records = [{"i": float(i)} for i in range(5)]
        result = split(records, test_ratio=0.4)
        assert isinstance(result, DataSplit)
        assert len(result.train) + len(result.test) == 5

    def test_feature_names_stored(self):
        records = [{"x": 1.0}]
        result = TrainTestSplitter().split(records, features=["x"])
        assert result.feature_names == ["x"]


class TestKFoldSplitter:
    def test_returns_k_folds(self):
        records = [{"i": float(i)} for i in range(10)]
        folds = KFoldSplitter(k=5).get_folds(records)
        assert len(folds) == 5

    def test_each_fold_has_test(self):
        records = [{"i": float(i)} for i in range(10)]
        for fold in KFoldSplitter(k=5).get_folds(records):
            assert len(fold.test) > 0

    def test_k_fold_split_function(self):
        records = [{"i": float(i)} for i in range(10)]
        folds = k_fold_split(records, k=2)
        assert len(folds) == 2


# ---------------------------------------------------------------------------
# models.linear
# ---------------------------------------------------------------------------


class TestLinearRegressor:
    def test_predict_before_fit_returns_zeros(self):
        m = LinearRegressor()
        preds = m.predict([[1.0, 2.0]])
        assert preds == [0.0]

    def test_fits_constant_target(self):
        X = [[1.0], [2.0], [3.0]]
        y = [5.0, 5.0, 5.0]
        m = LinearRegressor(lr=0.3, epochs=200)
        m.fit(X, y)
        preds = m.predict(X)
        for p in preds:
            assert abs(p - 5.0) < 0.5

    def test_fit_linear_function(self):
        X = [[float(i)] for i in range(5)]
        y = [float(i) for i in range(5)]
        m = fit_linear(X, y, lr=0.1, epochs=500)
        assert abs(m.predict_single([4.0]) - 4.0) < 0.5

    def test_predict_linear_function(self):
        X = [[1.0], [2.0]]
        y = [2.0, 4.0]
        m = fit_linear(X, y, lr=0.1, epochs=300)
        result = predict_linear(m, [[3.0]])
        assert abs(result[0] - 6.0) < 1.0


# ---------------------------------------------------------------------------
# models.tree
# ---------------------------------------------------------------------------


class TestDecisionNode:
    def test_is_leaf_when_label_set(self):
        node = DecisionNode(label=1.0)
        assert node.is_leaf

    def test_not_leaf_when_no_label(self):
        node = DecisionNode(feature_idx=0, threshold=0.5)
        assert not node.is_leaf


class TestDecisionTreeClassifier:
    def test_fit_and_predict_binary(self):
        X = [[0.0], [1.0], [2.0], [3.0]]
        y = [0.0, 0.0, 1.0, 1.0]
        t = DecisionTreeClassifier(max_depth=2)
        t.fit(X, y)
        preds = t.predict(X)
        assert preds[0] == 0.0
        assert preds[3] == 1.0

    def test_predict_single(self):
        X = [[0.0], [1.0], [2.0], [3.0]]
        y = [0.0, 0.0, 1.0, 1.0]
        t = fit_tree(X, y, max_depth=2)
        assert t.predict_single([0.0]) == 0.0
        assert t.predict_single([3.0]) == 1.0

    def test_pure_class_returns_that_class(self):
        X = [[0.0], [1.0]]
        y = [1.0, 1.0]
        t = DecisionTreeClassifier()
        t.fit(X, y)
        assert all(p == 1.0 for p in t.predict(X))


# ---------------------------------------------------------------------------
# models.ensemble
# ---------------------------------------------------------------------------


class TestEnsembleModel:
    def test_averaging(self):
        class Const:
            def fit(self, X, y): pass
            def predict(self, X): return [2.0] * len(X)

        class Const3:
            def fit(self, X, y): pass
            def predict(self, X): return [4.0] * len(X)

        ens = EnsembleModel([Const(), Const3()])
        result = ens.predict([[0.0]])
        assert result == [3.0]

    def test_predict_ensemble_function(self):
        class Zero:
            def fit(self, X, y): pass
            def predict(self, X): return [0.0] * len(X)

        result = predict_ensemble([Zero(), Zero()], [[1.0]])
        assert result == [0.0]


class TestVotingEnsemble:
    def test_majority_vote(self):
        class One:
            def fit(self, X, y): pass
            def predict(self, X): return [1.0] * len(X)

        class Zero:
            def fit(self, X, y): pass
            def predict(self, X): return [0.0] * len(X)

        ens = VotingEnsemble([One(), One(), Zero()])
        result = ens.predict([[0.0]])
        assert result == [1.0]


# ---------------------------------------------------------------------------
# training.optimizer
# ---------------------------------------------------------------------------


class TestSGDOptimizer:
    def test_step_reduces_weight_with_positive_grad(self):
        opt = SGDOptimizer(lr=0.1)
        updated = opt.step([1.0], [1.0])
        assert updated[0] < 1.0

    def test_step_count_increments(self):
        opt = SGDOptimizer()
        opt.step([0.0], [0.0])
        assert opt._step_count == 1

    def test_momentum_accumulates(self):
        opt = SGDOptimizer(lr=0.1, momentum=0.9)
        opt.step([1.0], [1.0])
        opt.step([0.5], [0.0])
        assert opt._velocity[0] > 0


class TestAdamOptimizer:
    def test_step_returns_same_length(self):
        opt = AdamOptimizer()
        result = opt.step([1.0, 2.0], [0.1, 0.2])
        assert len(result) == 2

    def test_learning_rate_property(self):
        opt = AdamOptimizer(lr=0.005)
        assert opt.learning_rate == 0.005


# ---------------------------------------------------------------------------
# training.callbacks
# ---------------------------------------------------------------------------


class TestEarlyStopping:
    def test_does_not_stop_when_improving(self):
        es = EarlyStopping(patience=3)
        for val_loss in [1.0, 0.9, 0.8]:
            assert es.on_epoch_end(0, 0.0, val_loss)

    def test_stops_after_patience_exceeded(self):
        es = EarlyStopping(patience=2)
        es.on_epoch_end(0, 0.0, 1.0)  # best
        es.on_epoch_end(1, 0.0, 1.1)  # no improve
        result = es.on_epoch_end(2, 0.0, 1.1)  # no improve → stop
        assert not result

    def test_should_stop_property(self):
        es = EarlyStopping(patience=1)
        es.on_epoch_end(0, 0.0, 1.0)
        es.on_epoch_end(1, 0.0, 1.0)
        assert es.should_stop()


class TestLearningRateScheduler:
    def test_lr_decays_over_epochs(self):
        sched = LearningRateScheduler(decay=0.5)
        assert sched.get_lr(1.0, 0) == 1.0
        assert sched.get_lr(1.0, 1) == 0.5
        assert abs(sched.get_lr(1.0, 2) - 0.25) < 1e-9

    def test_on_epoch_end_returns_true(self):
        sched = LearningRateScheduler()
        assert sched.on_epoch_end(0, 0.5, 0.4)


class TestCheckpointSaver:
    def test_tracks_best_epoch(self):
        ckpt = CheckpointSaver()
        ckpt.on_epoch_end(0, 0.0, 1.0)
        ckpt.on_epoch_end(1, 0.0, 0.5)
        ckpt.on_epoch_end(2, 0.0, 0.8)
        assert ckpt.best_epoch == 1
        assert ckpt.best_loss == 0.5


# ---------------------------------------------------------------------------
# training.contracts
# ---------------------------------------------------------------------------


class TestTrainingConfig:
    def test_defaults(self):
        cfg = TrainingConfig()
        assert cfg.learning_rate == 0.01
        assert cfg.epochs == 100

    def test_custom_values(self):
        cfg = TrainingConfig(learning_rate=0.001, model_type="tree")
        assert cfg.model_type == "tree"


class TestEpochLog:
    def test_fields(self):
        log = EpochLog(epoch=3, loss=0.05, val_loss=0.07)
        assert log.epoch == 3
        assert log.val_loss == 0.07


# ---------------------------------------------------------------------------
# training.trainer
# ---------------------------------------------------------------------------


class TestModelTrainer:
    def _simple_model(self):
        m = LinearRegressor(lr=0.1, epochs=50)
        return m

    def test_train_returns_loss(self):
        m = self._simple_model()
        trainer = ModelTrainer(m)
        X = [[1.0], [2.0], [3.0]]
        y = [1.0, 2.0, 3.0]
        result = trainer.train(X, y)
        assert "loss" in result
        assert result["loss"] >= 0

    def test_logs_recorded(self):
        m = self._simple_model()
        trainer = ModelTrainer(m)
        trainer.train([[1.0]], [1.0])
        assert len(trainer.logs) == 1

    def test_evaluate_step_returns_float(self):
        m = self._simple_model()
        trainer = ModelTrainer(m)
        trainer.train([[1.0]], [1.0])
        mse = trainer.evaluate_step([[1.0]], [1.0])
        assert isinstance(mse, float)

    def test_train_function(self):
        m = LinearRegressor(lr=0.1, epochs=50)
        result = train(m, [[1.0], [2.0]], [1.0, 2.0])
        assert "epochs_run" in result

    def test_evaluate_step_function(self):
        m = LinearRegressor(lr=0.1, epochs=50)
        m.fit([[1.0]], [1.0])
        mse = evaluate_step(m, [[1.0]], [1.0])
        assert mse >= 0


# ---------------------------------------------------------------------------
# evaluation.metrics
# ---------------------------------------------------------------------------


class TestAccuracy:
    def test_all_correct(self):
        acc = Accuracy()
        assert acc.compute([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_half_correct(self):
        acc = Accuracy()
        assert acc.compute([1.0, 0.0], [1.0, 1.0]) == 0.5

    def test_empty(self):
        assert Accuracy().compute([], []) == 0.0

    def test_compute_accuracy_function(self):
        assert compute_accuracy([1.0], [1.0]) == 1.0


class TestMSE:
    def test_zero_error(self):
        assert MSE().compute([2.0, 3.0], [2.0, 3.0]) == 0.0

    def test_known_mse(self):
        # errors = [1, 1] → mse = 1.0
        assert MSE().compute([1.0, 2.0], [0.0, 3.0]) == 1.0

    def test_compute_mse_function(self):
        assert compute_mse([0.0], [0.0]) == 0.0


class TestMAE:
    def test_zero_error(self):
        assert MAE().compute([1.0], [1.0]) == 0.0

    def test_known_mae(self):
        assert MAE().compute([0.0, 4.0], [2.0, 2.0]) == 2.0


# ---------------------------------------------------------------------------
# evaluation.evaluator
# ---------------------------------------------------------------------------


class TestModelEvaluator:
    def _fitted_model(self):
        m = LinearRegressor(lr=0.1, epochs=100)
        m.fit([[0.0], [1.0]], [0.0, 1.0])
        return m

    def test_returns_three_results_by_default(self):
        m = self._fitted_model()
        results = ModelEvaluator().evaluate(m, [[0.0], [1.0]], [0.0, 1.0])
        assert len(results) == 3

    def test_all_results_are_eval_result(self):
        m = self._fitted_model()
        results = ModelEvaluator().evaluate(m, [[0.0]], [0.0])
        assert all(isinstance(r, EvalResult) for r in results)

    def test_aggregate_metrics_keys(self):
        m = self._fitted_model()
        results = ModelEvaluator().evaluate(m, [[0.0]], [0.0])
        agg = ModelEvaluator().aggregate_metrics(results)
        assert "accuracy" in agg
        assert "mse" in agg

    def test_evaluate_function(self):
        m = self._fitted_model()
        results = evaluate(m, [[0.0]], [0.0])
        assert len(results) > 0

    def test_aggregate_metrics_function(self):
        results = [EvalResult("acc", 0.9), EvalResult("mse", 0.1)]
        agg = aggregate_metrics(results)
        assert agg["acc"] == 0.9


# ---------------------------------------------------------------------------
# evaluation.reporter
# ---------------------------------------------------------------------------


class TestEvaluationReport:
    def test_summary_contains_sandbox_name(self):
        report = EvaluationReport("my_sandbox")
        report.add_results([EvalResult("mse", 0.01)])
        s = report.summary()
        assert s["sandbox"] == "my_sandbox"

    def test_summary_count(self):
        report = EvaluationReport()
        report.add_results([EvalResult("a", 1.0), EvalResult("b", 2.0)])
        assert report.summary()["count"] == 2

    def test_metrics_in_summary(self):
        report = EvaluationReport()
        report.add_results([EvalResult("accuracy", 0.95)])
        assert report.summary()["metrics"]["accuracy"] == 0.95


class TestFormatReport:
    def test_format_contains_metric_name(self):
        results = [EvalResult("mse", 0.0123)]
        text = format_report(results)
        assert "mse" in text
        assert "0.0123" in text

    def test_multiple_lines(self):
        results = [EvalResult("a", 1.0), EvalResult("b", 2.0)]
        text = format_report(results)
        assert text.count("\n") == 1


class TestSummarize:
    def test_returns_dict_with_metric_names(self):
        m = LinearRegressor(lr=0.1, epochs=50)
        m.fit([[0.0], [1.0]], [0.0, 1.0])
        result = summarize(m, [[0.0], [1.0]], [0.0, 1.0])
        assert isinstance(result, dict)
        assert "accuracy" in result
