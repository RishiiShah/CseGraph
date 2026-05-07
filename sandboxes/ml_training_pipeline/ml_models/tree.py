from typing import Dict, List, Optional

from ml_models.contracts import Model


class DecisionNode:
    def __init__(
        self,
        feature_idx: int = -1,
        threshold: float = 0.0,
        label: Optional[float] = None,
        left: Optional["DecisionNode"] = None,
        right: Optional["DecisionNode"] = None,
    ) -> None:
        self.feature_idx = feature_idx
        self.threshold = threshold
        self.label = label
        self.left = left
        self.right = right

    @property
    def is_leaf(self) -> bool:
        return self.label is not None


class DecisionTreeClassifier:
    def __init__(self, max_depth: int = 3) -> None:
        self._max_depth = max_depth
        self._root: Optional[DecisionNode] = None

    def fit(self, X: List[List[float]], y: List[float]) -> None:
        self._root = self._build(X, y, depth=0)

    def _gini(self, y: List[float]) -> float:
        if not y:
            return 0.0
        n = len(y)
        counts: Dict[float, int] = {}
        for val in y:
            counts[val] = counts.get(val, 0) + 1
        return 1.0 - sum((c / n) ** 2 for c in counts.values())

    def _build(
        self, X: List[List[float]], y: List[float], depth: int
    ) -> DecisionNode:
        if not y or depth >= self._max_depth or len(set(y)) == 1:
            majority = max(set(y), key=y.count) if y else 0.0
            return DecisionNode(label=majority)

        best_gain, best_feat, best_thresh = -1.0, 0, 0.0
        base_gini = self._gini(y)
        n_features = len(X[0])

        for feat in range(n_features):
            vals = sorted(set(row[feat] for row in X))
            for i in range(len(vals) - 1):
                thresh = (vals[i] + vals[i + 1]) / 2
                left_y = [y[j] for j, row in enumerate(X) if row[feat] <= thresh]
                right_y = [y[j] for j, row in enumerate(X) if row[feat] > thresh]
                if not left_y or not right_y:
                    continue
                gain = base_gini - (
                    len(left_y) / len(y) * self._gini(left_y)
                    + len(right_y) / len(y) * self._gini(right_y)
                )
                if gain > best_gain:
                    best_gain, best_feat, best_thresh = gain, feat, thresh

        left_X = [X[j] for j in range(len(X)) if X[j][best_feat] <= best_thresh]
        left_y = [y[j] for j in range(len(X)) if X[j][best_feat] <= best_thresh]
        right_X = [X[j] for j in range(len(X)) if X[j][best_feat] > best_thresh]
        right_y = [y[j] for j in range(len(X)) if X[j][best_feat] > best_thresh]

        if not left_X or not right_X:
            majority = max(set(y), key=y.count)
            return DecisionNode(label=majority)

        return DecisionNode(
            feature_idx=best_feat,
            threshold=best_thresh,
            left=self._build(left_X, left_y, depth + 1),
            right=self._build(right_X, right_y, depth + 1),
        )

    def predict(self, X: List[List[float]]) -> List[float]:
        return [self._predict_row(row, self._root) for row in X]

    def _predict_row(self, row: List[float], node: DecisionNode) -> float:
        if node.is_leaf:
            return node.label
        if row[node.feature_idx] <= node.threshold:
            return self._predict_row(row, node.left)
        return self._predict_row(row, node.right)

    def predict_single(self, features: List[float]) -> float:
        return self._predict_row(features, self._root)


def fit_tree(
    X: List[List[float]], y: List[float], max_depth: int = 3
) -> DecisionTreeClassifier:
    t = DecisionTreeClassifier(max_depth=max_depth)
    t.fit(X, y)
    return t


def predict_tree(
    model: DecisionTreeClassifier, X: List[List[float]]
) -> List[float]:
    return model.predict(X)
