"""
Prediccion BUY/HOLD/SELL sobre la ultima barra cerrada (day trading 1h).

Carga el modelo entrenado (core/nvda_trainer.py), construye las features
con los mismos calculos del entrenamiento y devuelve etiqueta + probabilidades.

Regla de señal: si BUY/SELL no supera SIGNAL_PROBA_THRESHOLD -> HOLD,
para reducir ruido y costes de operar cada hora.

Consistencia train/serve:
  - Nunca predice sobre una barra en curso (mismo criterio que labels.py).
  - El entrenamiento con etiquetado intra-sesion (H barras) solo cubre las
    primeras (INTRADAY_BARS_PER_DAY - H) barras de cada sesion. Si la barra
    actual esta FUERA de ese soporte, se avisa (o se suprime la señal con
    PREDICT_STRICT_SUPPORT=1) para no emitir extrapolaciones no validadas.
"""
import json
import logging
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from core.config import (
    DAYTRADE_HORIZON_BARS,
    INTRADAY_BARS_PER_DAY,
    MODELS_DIR,
    PREDICT_STRICT_SUPPORT,
    SIGNAL_PROBA_THRESHOLD,
)

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")


class ModelNotTrainedError(FileNotFoundError):
    """No existen los artefactos del modelo (nunca se ha entrenado)."""


def _load_artifacts(model_name: str = "logreg"):
    model_path = MODELS_DIR / f"modelo_nvda_{model_name}.pkl"
    model_feature_path = MODELS_DIR / f"feature_list_{model_name}.json"
    feature_path = model_feature_path if model_feature_path.exists() else MODELS_DIR / "feature_list.json"
    label_path = MODELS_DIR / "label_map.json"

    if not model_path.exists():
        raise ModelNotTrainedError(
            f"No existe {model_path}. Entrena primero: python run_nvda.py --train"
        )
    if not feature_path.exists():
        raise ModelNotTrainedError(f"Falta {feature_path} (artefacto del entrenamiento)")

    model = joblib.load(model_path)
    features = json.loads(feature_path.read_text(encoding="utf-8"))
    label_map = {int(k): v for k, v in json.loads(label_path.read_text(encoding="utf-8")).items()}
    return model, features, label_map


def predict_latest(model_name: str = "logreg", include_patterns: bool = False,
                   threshold: Optional[float] = None) -> Dict:
    """
    Predice sobre la ultima barra con features validas.

    Returns:
        dict con label, label_sin_umbral, probabilidades, datetime_barra,
        close, threshold y modelo.
    """
    from core.feature_engineering import build_features

    model, features, label_map = _load_artifacts(model_name)

    df = build_features(include_patterns=include_patterns, save=False)
    if df is None or df.empty:
        raise RuntimeError("No se pudieron construir features (faltan datos intradia)")

    # 1. Nunca predecir sobre barra en curso (mismo criterio que labels.py:
    #    la barra con timestamp t cubre [t, t+1h); las barras son de 1 hora).
    now_utc = pd.Timestamp.now(tz="UTC")
    if len(df) and pd.to_datetime(df["Datetime"].iloc[-1], utc=True) + pd.Timedelta(hours=1) > now_utc:
        logger.info("Ultima barra en curso: se predice sobre la ultima barra CERRADA")
        df = df.iloc[:-1].reset_index(drop=True)
    if df.empty:
        raise RuntimeError("No hay barras cerradas para predecir")

    advertencias = []

    # 2. Consistencia train/serve: el etiquetado intra-sesion (H barras)
    #    solo entrena con las primeras (BARS_PER_DAY - H) barras de cada dia.
    max_supported_bar = INTRADAY_BARS_PER_DAY - DAYTRADE_HORIZON_BARS
    fuera_de_soporte = False
    if "bar_of_day" in df.columns:
        bar_of_day = int(df["bar_of_day"].iloc[-1])
        fuera_de_soporte = bar_of_day > max_supported_bar
        if fuera_de_soporte:
            advertencias.append(
                f"Barra {bar_of_day} de {INTRADAY_BARS_PER_DAY} fuera del soporte de "
                f"entrenamiento (solo barras 1-{max_supported_bar} tienen label con H={DAYTRADE_HORIZON_BARS})"
            )

    missing = [c for c in features if c not in df.columns]
    for c in missing:
        logger.warning(f"Feature '{c}' ausente en inferencia - se rellena con 0")
        df[c] = 0.0
    if missing:
        advertencias.append(f"{len(missing)} features ausentes rellenadas con 0: {missing}")

    X = df[features].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if X.empty:
        raise RuntimeError("Ninguna fila con features para predecir")

    last_idx = X.index[-1]
    row = X.loc[[last_idx]]
    proba = model.predict_proba(row)[0]
    idx = int(np.argmax(proba))

    probs = {label_map.get(i, str(i)): round(float(proba[i]), 4) for i in range(len(proba))}
    raw_label = label_map.get(idx, "HOLD")
    thr = SIGNAL_PROBA_THRESHOLD if threshold is None else threshold

    label = raw_label
    if raw_label == "BUY" and probs.get("BUY", 0) < thr:
        label = "HOLD"
    elif raw_label == "SELL" and probs.get("SELL", 0) < thr:
        label = "HOLD"

    # 3. Modo estricto: fuera del soporte de entrenamiento no se emite señal.
    if fuera_de_soporte and PREDICT_STRICT_SUPPORT:
        label = "SIN_SENAL"

    for w in advertencias:
        logger.warning(f"ADVERTENCIA prediccion: {w}")

    bar_dt = pd.to_datetime(df.loc[last_idx, "Datetime"], utc=True)
    return {
        "label": label,
        "label_sin_umbral": raw_label,
        "probabilidades": probs,
        "datetime_barra": bar_dt.isoformat(),
        "datetime_barra_ny": bar_dt.tz_convert(NY_TZ).strftime("%Y-%m-%d %H:%M ET"),
        "close": float(df.loc[last_idx, "Close"]) if "Close" in df.columns else None,
        "threshold": thr,
        "modelo": model_name,
        "direccion": ("SUBE" if label == "BUY" else ("BAJA" if label == "SELL" else "LATERAL")),
        "fuera_de_soporte_entrenamiento": fuera_de_soporte,
        "advertencias": advertencias,
    }


def format_signal_message(pred: Dict) -> str:
    """Mensaje de alerta para Synology/consola."""
    icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "SIN_SENAL": "⚪"}.get(pred["label"], "⚪")
    p = pred["probabilidades"]
    lineas = [
        f"{icon} *NVDA DAY TRADING — SEÑAL {pred['label']}*",
        f"Barra: {pred['datetime_barra_ny']} | Close: {pred['close']}",
        "",
        f"BUY:  {p.get('BUY', 0) * 100:.1f}%",
        f"HOLD: {p.get('HOLD', 0) * 100:.1f}%",
        f"SELL: {p.get('SELL', 0) * 100:.1f}%",
        "",
        f"Modelo: {pred['modelo'].upper()} | umbral: {pred['threshold'] * 100:.0f}%",
    ]
    if pred.get("fuera_de_soporte_entrenamiento"):
        lineas.append("⚠️ Barra fuera del soporte de entrenamiento — señal no validada.")
    for w in pred.get("advertencias", []):
        lineas.append(f"⚠️ {w}")
    lineas.append("_Señal de apoyo a decisiones, no asesoría financiera._")
    return "\n".join(lineas)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    prediccion = predict_latest()
    print(format_signal_message(prediccion))
