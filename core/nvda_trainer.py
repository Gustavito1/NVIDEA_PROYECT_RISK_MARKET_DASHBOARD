#!/usr/bin/env python
"""
Entrenamiento y evaluacion de modelos BUY/HOLD/SELL (day trading).

Modelos:
  1. Logistic Regression (baseline, con StandardScaler)
  2. Random Forest (principal)
  3. XGBoost (opcional, si esta instalado)

Evaluacion:
  Walk-forward expanding con purging + embargo (sin look-ahead):
      [==== train ====(purge)(embargo)][ test ]
  Metricas: accuracy, F1 macro, precision/recall por clase, matriz de
  confusion y ROC-AUC OVR sobre predicciones out-of-fold.

Experimento principal:
  Modelo A (solo mercado) vs Modelo B (mercado + sentimiento)

Uso:
    python -m core.nvda_trainer
    python -m core.nvda_trainer --folds 5 --min-train 0.5
"""
import argparse
import json
import logging
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from core.config import (
    LABELED_CSV,
    MODELS_DIR,
    MODEL_EMBARGO_BARS,
    MODEL_PURGE_BARS,
    MODEL_USE_NEWS_COUNTS,
    MODEL_USE_PATTERNS,
    NON_FEATURE_COLUMNS,
)

logger = logging.getLogger(__name__)

LABELS = ["BUY", "HOLD", "SELL"]
LABEL_TO_IDX = {lab: i for i, lab in enumerate(LABELS)}
IDX_TO_LABEL = {i: lab for i, lab in enumerate(LABELS)}
SENTIMENT_PREFIXES = ("sent_", "cnt_", "bull_ratio", "hours_since_news", "has_sentiment")
SOX_PREFIXES = ("sox_", "beta_", "corr_", "alpha_", "rel_strength_")


def load_dataset() -> pd.DataFrame:
    if not LABELED_CSV.exists():
        raise FileNotFoundError(
            f"No existe {LABELED_CSV}. Corre primero: python run_nvda.py --skip-intradia"
        )
    df = pd.read_csv(LABELED_CSV)
    df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
    return df.sort_values("Datetime").reset_index(drop=True)


def feature_columns(df: pd.DataFrame) -> List[str]:
    """
    Columnas que entran al modelo.

    Excluye por diseno (ver resultados.md 2026-09-20):
      - `pat_*`: 36 patrones binarios que solo anaden ruido (medido).
      - `cnt_*` / `has_sentiment*`: conteo de noticias; captura la densidad del
        dataset (drift 1.6 -> 120 articulos/semana) y rompe el holdout.
    Se pueden reactivar con MODEL_USE_PATTERNS / MODEL_USE_NEWS_COUNTS.
    """
    cols = []
    for c in df.columns:
        if c in NON_FEATURE_COLUMNS:
            continue
        if c.startswith("label") or c.startswith("future_ret"):
            continue
        if not MODEL_USE_PATTERNS and c.startswith("pat_"):
            continue
        if not MODEL_USE_NEWS_COUNTS and (c.startswith("cnt_") or c.startswith("has_sentiment")):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)

    # Guarda anti-leakage: niveles de precio crudo jamás deben ser features
    # (no estacionarios; el modelo aprendería "regimen de precio/epoca").
    prohibidas = {"Open", "High", "Low", "Close", "Volume", "Adj Close",
                  "adj_close", "sox_close", "vwap", "orb_high", "orb_low"}
    intrusas = prohibidas.intersection(cols)
    if intrusas:
        raise ValueError(
            f"Columnas de precio crudo filtrarian informacion al modelo: {sorted(intrusas)}"
        )
    return cols


def _is_sentiment(col: str) -> bool:
    return any(col.startswith(p) for p in SENTIMENT_PREFIXES)


def _is_sox(col: str) -> bool:
    return any(col.startswith(p) for p in SOX_PREFIXES)


def feature_sets(df: pd.DataFrame) -> Dict[str, List[str]]:
    """Define ablations para medir el aporte real de SOXQ y noticias."""
    all_features = feature_columns(df)
    nvda_only = [c for c in all_features if not _is_sox(c) and not _is_sentiment(c)]
    nvda_sox = [c for c in all_features if not _is_sentiment(c)]
    nvda_news = [c for c in all_features if not _is_sox(c)]
    return {
        "A_nvda_only": nvda_only,
        "B_nvda_sox": nvda_sox,
        "C_nvda_news": nvda_news,
        "D_nvda_sox_news": all_features,
    }


def purged_walk_forward(n: int, n_splits: int = 5, purge: int = 3,
                        embargo: int = 7, min_train_fraction: float = 0.5):
    """Genera folds temporales expanding con purge + embargo antes del test."""
    min_train = max(int(n * min_train_fraction), 60)
    remaining = n - min_train
    if remaining < n_splits:
        raise ValueError("Datos insuficientes para walk-forward")
    test_size = remaining // n_splits

    for k in range(n_splits):
        test_start = min_train + k * test_size
        test_end = test_start + test_size if k < n_splits - 1 else n
        train_end = test_start - purge - embargo
        if train_end < 40:
            continue
        yield np.arange(0, train_end), np.arange(test_start, test_end)


def development_test_split(df: pd.DataFrame, test_fraction: float = 0.2):
    """Separa por sesiones completas; el último bloque queda intocable."""
    if "date_ny" in df.columns:
        dates = pd.Series(df["date_ny"].astype(str)).drop_duplicates().sort_values().to_numpy()
    else:
        dates = (
            pd.to_datetime(df["Datetime"], utc=True)
            .dt.tz_convert("America/New_York")
            .dt.strftime("%Y-%m-%d")
            .drop_duplicates()
            .sort_values()
            .to_numpy()
        )
    split = max(1, int(len(dates) * (1 - test_fraction)))
    test_start = dates[split]
    date_values = df["date_ny"].astype(str) if "date_ny" in df.columns else pd.to_datetime(
        df["Datetime"], utc=True
    ).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    development = df[date_values < test_start].copy().reset_index(drop=True)
    test = df[date_values >= test_start].copy().reset_index(drop=True)
    return development, test, test_start


def evaluate_holdout(model_name: str, train_df: pd.DataFrame, test_df: pd.DataFrame,
                     features: List[str]) -> Optional[Dict]:
    """Evalúa una sola vez el bloque temporal final no usado en selección."""
    if train_df.empty or test_df.empty:
        return None
    y_train = train_df["label"].map(LABEL_TO_IDX).to_numpy()
    y_test = test_df["label"].map(LABEL_TO_IDX).to_numpy()
    model = build_model(model_name)
    if model is None:
        return None
    X_train = train_df[features].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    X_test = test_df[features].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    result = {
        "accuracy_final_test": round(float(accuracy_score(y_test, pred)), 4),
        "f1_macro_final_test": round(float(f1_score(y_test, pred, average="macro", zero_division=0)), 4),
        "confusion_matrix_final_test": confusion_matrix(y_test, pred, labels=[0, 1, 2]).tolist(),
        "n_final_test": int(len(test_df)),
    }
    if hasattr(model, "predict_proba"):
        try:
            proba = model.predict_proba(X_test)
            result["roc_auc_final_test"] = round(float(
                roc_auc_score(y_test, proba, multi_class="ovr", labels=[0, 1, 2])
            ), 4)
        except Exception:
            pass
    return result


def build_model(name: str):
    if name == "logreg":
        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced")),
        ])
    if name == "rf":
        return RandomForestClassifier(
            n_estimators=400,
            max_depth=8,
            min_samples_leaf=5,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )
    if name == "xgb":
        try:
            from xgboost import XGBClassifier
        except ImportError:
            return None
        return XGBClassifier(
            n_estimators=400,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            tree_method="hist",
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
        )
    raise ValueError(f"Modelo desconocido: {name}")


def evaluate_oof(model_name: str, X: pd.DataFrame, y: np.ndarray,
                 n_splits: int, min_train: float) -> Tuple[Optional[Dict], Optional[np.ndarray]]:
    """Evalua con walk-forward y devuelve metricas + predicciones OOF."""
    n = len(X)
    oof_pred = np.full(n, -1, dtype=int)
    oof_proba = np.full((n, len(LABELS)), np.nan)
    fold_f1 = []

    X = X.astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    for train_idx, test_idx in purged_walk_forward(
            n, n_splits=n_splits, purge=MODEL_PURGE_BARS,
            embargo=MODEL_EMBARGO_BARS, min_train_fraction=min_train):
        model = build_model(model_name)
        if model is None:
            return None, None
        model.fit(X.iloc[train_idx], y[train_idx])
        pred = model.predict(X.iloc[test_idx])
        oof_pred[test_idx] = pred
        if hasattr(model, "predict_proba"):
            oof_proba[test_idx] = model.predict_proba(X.iloc[test_idx])
        fold_f1.append(f1_score(y[test_idx], pred, average="macro", zero_division=0))

    mask = oof_pred >= 0
    if mask.sum() < 30:
        return None, None

    y_true = y[mask]
    y_pred = oof_pred[mask]
    metrics = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "f1_macro": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4),
        "f1_weighted": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4),
        "f1_folds_mean": round(float(np.mean(fold_f1)), 4),
        "f1_folds_std": round(float(np.std(fold_f1)), 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=[0, 1, 2]).tolist(),
        "n_eval": int(mask.sum()),
    }
    prec, rec, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=[0, 1, 2], zero_division=0)
    for i, lab in enumerate(LABELS):
        metrics[f"precision_{lab}"] = round(float(prec[i]), 4)
        metrics[f"recall_{lab}"] = round(float(rec[i]), 4)
        metrics[f"support_{lab}"] = int(sup[i])

    try:
        proba_valid = oof_proba[mask]
        if not np.isnan(proba_valid).any():
            metrics["roc_auc_ovr"] = round(float(
                roc_auc_score(y_true, proba_valid, multi_class="ovr", labels=[0, 1, 2])
            ), 4)
    except Exception:
        pass

    return metrics, oof_proba


def _baseline_majority(y: np.ndarray, n_splits: int, min_train: float) -> Dict:
    y_pred = np.zeros(len(y), dtype=int)
    evaluated = np.zeros(len(y), dtype=bool)
    for train_idx, test_idx in purged_walk_forward(
            len(y), n_splits=n_splits, purge=MODEL_PURGE_BARS,
            embargo=MODEL_EMBARGO_BARS, min_train_fraction=min_train):
        majority = np.bincount(y[train_idx]).argmax()
        y_pred[test_idx] = majority
        evaluated[test_idx] = True
    mask = evaluated
    return {
        "accuracy": round(float(accuracy_score(y[mask], y_pred[mask])), 4),
        "f1_macro": round(float(f1_score(y[mask], y_pred[mask], average="macro", zero_division=0)), 4),
        "n_eval": int(mask.sum()),
    }


def run_experiment(df: pd.DataFrame, n_splits: int, min_train: float,
                   test_fraction: float = 0.2) -> Dict:
    """Compara ablations en desarrollo y reporta un test temporal final."""
    development, final_test, test_start = development_test_split(df, test_fraction)
    sets = feature_sets(df)
    logger.info(
        "Feature sets: " + ", ".join(f"{k}={len(v)}" for k, v in sets.items())
    )

    y = development["label"].map(LABEL_TO_IDX).to_numpy()

    results = {}
    baseline = _baseline_majority(y, n_splits, min_train)
    y_final = final_test["label"].map(LABEL_TO_IDX).to_numpy()
    majority = np.bincount(y).argmax()
    baseline["accuracy_final_test"] = round(float(accuracy_score(y_final, np.full(len(y_final), majority))), 4)
    baseline["f1_macro_final_test"] = round(float(
        f1_score(y_final, np.full(len(y_final), majority), average="macro", zero_division=0)
    ), 4)
    baseline["n_final_test"] = int(len(final_test))
    baseline["test_start"] = str(test_start)
    logger.info(f"Baseline (mayoria): accuracy={baseline['accuracy']} f1_macro={baseline['f1_macro']}")
    results["baseline_majority"] = baseline

    for name in ["logreg", "rf", "xgb"]:
        model_obj = build_model(name)
        if model_obj is None:
            logger.warning(f"{name}: no disponible (pip install xgboost)")
            continue

        for tag, feats in sets.items():
            key = f"{name}_{tag}"
            logger.info(f"Evaluando {key} ({len(feats)} features)...")
            metrics, _ = evaluate_oof(name, development[feats], y, n_splits, min_train)
            if metrics is None:
                logger.warning(f"{key}: sin metricas")
                continue
            metrics["n_features"] = len(feats)
            metrics.update(evaluate_holdout(name, development, final_test, feats) or {})
            metrics["test_start"] = str(test_start)
            results[key] = metrics
            logger.info(
                f"  {key}: F1macro={metrics['f1_macro']} acc={metrics['accuracy']} "
                f"ROC-AUC={metrics.get('roc_auc_ovr', 'n/a')}"
            )
    return results


def train_final(df: pd.DataFrame, model_name: str, use_sentiment: bool,
                n_splits: int, min_train: float,
                feature_set: Optional[str] = None) -> Optional[Dict]:
    """Entrena con todos los datos y guarda artefactos del modelo elegido."""
    sets = feature_sets(df)
    if feature_set is None:
        feature_set = "D_nvda_sox_news" if use_sentiment else "B_nvda_sox"
    if feature_set not in sets:
        raise ValueError(f"Feature set desconocido: {feature_set}")
    feats = sets[feature_set]

    y = df["label"].map(LABEL_TO_IDX).to_numpy()

    model = build_model(model_name)
    if model is None:
        logger.error(f"Modelo {model_name} no disponible")
        return None

    metrics, _ = evaluate_oof(model_name, df[feats], y, n_splits, min_train)

    X_final = df[feats].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    model.fit(X_final, y)
    model_path = MODELS_DIR / f"modelo_nvda_{model_name}.pkl"
    joblib.dump(model, model_path)
    with open(MODELS_DIR / f"feature_list_{model_name}.json", "w", encoding="utf-8") as f:
        json.dump(feats, f, indent=2, ensure_ascii=False)
    with open(MODELS_DIR / "feature_list.json", "w", encoding="utf-8") as f:
        json.dump(feats, f, indent=2, ensure_ascii=False)
    with open(MODELS_DIR / "label_map.json", "w", encoding="utf-8") as f:
        json.dump(IDX_TO_LABEL, f, indent=2)
    if metrics is not None:
        with open(MODELS_DIR / f"metrics_{model_name}.json", "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        with open(MODELS_DIR / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)

    logger.info(f"Modelo final guardado: {model_path} ({len(feats)} features)")

    fi = None
    if hasattr(model, "feature_importances_"):
        fi = sorted(zip(feats, model.feature_importances_), key=lambda x: -x[1])[:20]
        logger.info("Top 15 features (RF):")
        for name, imp in fi[:15]:
            logger.info(f"  {name}: {imp:.4f}")
    return {"model_path": str(model_path), "metrics": metrics, "features": feats,
            "top_importances": fi[:15] if fi else None}


def main():
    parser = argparse.ArgumentParser(description="Entrena modelos NVDA day trading")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train", type=float, default=0.5)
    parser.add_argument("--final-model", choices=["logreg", "rf", "xgb"], default="logreg")
    parser.add_argument("--no-sentiment", action="store_true",
                        help="Entrenar modelo final solo con features de mercado")
    parser.add_argument(
        "--feature-set",
        choices=["A_nvda_only", "B_nvda_sox", "C_nvda_news", "D_nvda_sox_news"],
        default=None,
        help="Ablacion final: NVDA, NVDA+SOX, NVDA+news o todos",
    )
    parser.add_argument("--skip-experiment", action="store_true",
                        help="Solo entrenar el modelo final (sin A vs B)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    df = load_dataset()
    logger.info(f"Dataset: {len(df)} filas | fechas {df['Datetime'].min().date()} -> {df['Datetime'].max().date()}")
    logger.info("Distribucion: " + str(df['label'].value_counts().to_dict()))

    if not args.skip_experiment:
        results = run_experiment(df, args.folds, args.min_train)
        with open(MODELS_DIR / "experiment_ab.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print("\n=== ABLACIONES NVDA / SOX / NOTICIAS ===")
        print(f"{'modelo':28s} {'F1 OOF':>8s} {'F1 TEST':>8s} {'AUC OOF':>8s} {'AUC TEST':>9s} {'n_feat':>7s}")
        for key, m in results.items():
            if "n_features" in m:
                print(f"{key:28s} {m['f1_macro']:>8.4f} {m.get('f1_macro_final_test', float('nan')):>8.4f} "
                      f"{m.get('roc_auc_ovr', float('nan')):>8.4f} "
                      f"{m.get('roc_auc_final_test', float('nan')):>9.4f} {m['n_features']:>7d}")
            else:
                print(f"{key:28s} {m['f1_macro']:>8.4f} {m.get('f1_macro_final_test', float('nan')):>8.4f} "
                      f"{'-':>8s} {'-':>9s} {'-':>7s}")
    else:
        logger.info("Experimento A vs B omitido (--skip-experiment)")

    final = train_final(
        df,
        args.final_model,
        use_sentiment=not args.no_sentiment,
        feature_set=args.feature_set,
        n_splits=args.folds,
        min_train=args.min_train,
    )
    if final is None:
        return 1
    print(f"\nModelo final: {final['model_path']}")
    if final["metrics"]:
        print(json.dumps(final["metrics"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
