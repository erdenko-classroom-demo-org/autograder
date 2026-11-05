"""Качество модели: предсказания студента против скрытых правильных ответов, перевод метрики в баллы по шкале."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import CheckContext, CheckResult, points_from_scale, register
from ..config import Check

HIGHER_IS_BETTER = {"r2": True, "accuracy": True, "f1": True, "f1_macro": True, "roc_auc": True,
                    "mae": False, "rmse": False, "mse": False, "mape": False, "logloss": False}


def compute_metric(name: str, y_true: np.ndarray, y_pred: np.ndarray) -> float:
    name = name.lower()
    if name == "r2":
        ss_res = float(np.sum((y_true - y_pred) ** 2))
        ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
        return 1 - ss_res / ss_tot if ss_tot else 0.0
    if name == "mae":
        return float(np.mean(np.abs(y_true - y_pred)))
    if name == "mse":
        return float(np.mean((y_true - y_pred) ** 2))
    if name == "rmse":
        return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    if name == "mape":
        return float(np.mean(np.abs((y_true - y_pred) / np.where(y_true == 0, np.nan, y_true))) * 100)
    if name == "accuracy":
        return float(np.mean(y_true == y_pred))
    if name in ("f1", "f1_macro", "roc_auc", "logloss"):
        from sklearn import metrics as skm  # необязательная зависимость

        return {
            "f1": lambda: skm.f1_score(y_true, y_pred),
            "f1_macro": lambda: skm.f1_score(y_true, y_pred, average="macro"),
            "roc_auc": lambda: skm.roc_auc_score(y_true, y_pred),
            "logloss": lambda: skm.log_loss(y_true, y_pred),
        }[name]()
    raise ValueError(f"неизвестная метрика {name}")


@register("metric_threshold")
def metric_threshold(ctx: CheckContext, check: Check) -> CheckResult:
    """spec:
        predictions: outputs/predictions.csv       — файл студента
        truth: private:data/test_target.csv        — скрытые ответы
        id_column: id
        target_column: price                       — колонка и в предсказаниях, и в ответах
        metric: r2 | mae | rmse | mse | mape | accuracy | f1 | f1_macro | roc_auc | logloss
        scale: [{min: 0.80, points: 4}, {min: 0.70, points: 3}, ...]   # для «меньше — лучше» используйте max
    """
    pred_path = ctx.resolve(check.spec["predictions"])
    truth_path = ctx.resolve(check.spec["truth"])
    if not pred_path.is_file():
        return CheckResult(check.id, 0, check.points, False, f"нет файла {check.spec['predictions']}")
    idc, tc = check.spec.get("id_column", "id"), check.spec["target_column"]
    try:
        pred = pd.read_csv(pred_path)
    except Exception as e:
        return CheckResult(check.id, 0, check.points, False, f"предсказания не читаются как CSV: {e}")
    truth = pd.read_csv(truth_path)
    for col, where in ((idc, "предсказаниях"), (tc, "предсказаниях")):
        if col not in pred.columns:
            return CheckResult(check.id, 0, check.points, False, f"в {where} нет колонки '{col}'")
    if pred[idc].duplicated().any():
        return CheckResult(check.id, 0, check.points, False, "повторяющиеся id в предсказаниях")
    merged = truth[[idc, tc]].merge(pred[[idc, tc]], on=idc, how="left", suffixes=("_true", "_pred"))
    n_missing = int(merged[f"{tc}_pred"].isna().sum())
    if n_missing:
        return CheckResult(check.id, 0, check.points, False,
                           f"нет предсказаний для {n_missing} из {len(truth)} объектов (или пропуски)")
    y_true = merged[f"{tc}_true"].to_numpy(dtype=float)
    y_pred = merged[f"{tc}_pred"].to_numpy(dtype=float)
    metric = check.spec.get("metric", "r2")
    value = compute_metric(metric, y_true, y_pred)
    hib = check.spec.get("higher_is_better", HIGHER_IS_BETTER.get(metric.lower(), True))
    pts, why = points_from_scale(value, check.spec["scale"], hib)
    return CheckResult(check.id, pts, check.points, pts > 0, f"{metric} = {value:.4f} → {pts:g} б. ({why})",
                       details={"metric": metric, "value": value})
