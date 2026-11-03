"""
Target BUY / HOLD / SELL para DAY TRADING (barras de 1 hora).

Regla:
    future_ret = Close[t+H] / Close[t] - 1
    BUY  si future_ret >  tau
    SELL si future_ret < -tau
    HOLD en caso contrario

tau:
  - "adaptive" (default): tau = k * std(ret_1b, 20) recortado a [min, max]
  - "fixed": tau = MODEL_TAU_PCT / 100

Guarda label_start / label_end para purging temporal (Lopez de Prado).

Salida: core/output/features/nvda_labeled.csv
"""
import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from core.config import (
    DAYTRADE_HORIZON_BARS,
    MODEL_TARGET_MODE,
    DAYTRADE_TAU_K,
    DAYTRADE_TAU_MAX,
    DAYTRADE_TAU_MIN,
    DAYTRADE_TAU_MODE,
    DAYTRADE_TAU_PCT,
    FEATURES_CSV,
    LABELED_CSV,
)

logger = logging.getLogger(__name__)

LABEL_BUY = "BUY"
LABEL_HOLD = "HOLD"
LABEL_SELL = "SELL"


def bar_end_time(ts) -> pd.Timestamp:
    """
    Fin de la barra cuyo timestamp es su INICIO.

    La primera barra RTH (09:30 ET, timestamp con minuto :30) dura 30 min
    (09:30-10:00); el resto de barras RTH duran 60 min. Se usa para detectar
    la barra en curso sin cometer un off-by-one en la primera barra del dia.
    """
    t = pd.to_datetime(ts, utc=True)
    return t + (pd.Timedelta(minutes=30) if t.minute == 30 else pd.Timedelta(hours=1))


def _compute_tau(df: pd.DataFrame, mode: str, k: float, tau_pct: float,
                 tau_min: float, tau_max: float) -> pd.Series:
    vol_col = "log_ret_1b" if MODEL_TARGET_MODE == "log" and "log_ret_1b" in df else "ret_1b"
    if mode == "fixed":
        tau = pd.Series(tau_pct / 100.0, index=df.index)
    else:
        vol = df[vol_col].rolling(20).std()
        tau = k * vol
    if MODEL_TARGET_MODE == "log":
        tau = np.log1p(tau)
    return tau.clip(lower=tau_min, upper=tau_max)


def compute_labels(df: pd.DataFrame,
                   horizon_bars: Optional[int] = None,
                   tau_mode: Optional[str] = None) -> Tuple[pd.DataFrame, dict]:
    """
    Anade future_ret, tau, label, label_start y label_end.

    Returns:
        (df con labels, resumen de distribucion)
    """
    horizon = horizon_bars or DAYTRADE_HORIZON_BARS
    mode = tau_mode or DAYTRADE_TAU_MODE

    out = df.copy().reset_index(drop=True)

    # Si la ultima barra aun no ha cerrado (descarga a mitad de sesion), no puede
    # usarse como futuro ni etiquetarse.
    now_utc = pd.Timestamp.now(tz="UTC")
    if len(out) and bar_end_time(out["Datetime"].iloc[-1]) > now_utc:
        logger.info("Ultima barra en curso: se excluye del etiquetado")
        out = out.iloc[:-1].reset_index(drop=True)

    if "adj_close" in out.columns:
        price_col = "adj_close"
    elif "Adj Close" in out.columns:
        price_col = "Adj Close"
    else:
        price_col = "Close"
    price = out[price_col]
    if "date_ny" not in out.columns:
        out["date_ny"] = pd.to_datetime(out["Datetime"], utc=True).dt.tz_convert(
            "America/New_York"
        ).dt.date
    session = out["date_ny"]
    future_price = price.groupby(session, sort=False).shift(-horizon)
    future_datetime = out["Datetime"].groupby(session, sort=False).shift(-horizon)

    if MODEL_TARGET_MODE == "log":
        out["future_ret"] = np.log(future_price / price)
    else:
        out["future_ret"] = future_price / price - 1
    out["future_ret_mode"] = MODEL_TARGET_MODE
    out["future_ret_price"] = price_col

    out["tau"] = _compute_tau(out, mode, DAYTRADE_TAU_K, DAYTRADE_TAU_PCT,
                              DAYTRADE_TAU_MIN, DAYTRADE_TAU_MAX)

    out["label"] = np.select(
        [out["future_ret"] > out["tau"], out["future_ret"] < -out["tau"]],
        [LABEL_BUY, LABEL_SELL],
        default=LABEL_HOLD,
    )

    out["label_start"] = out["Datetime"]
    out["label_end"] = future_datetime
    out["label_horizon_bars"] = horizon

    valid = out["future_ret"].notna() & out["tau"].notna()
    dropped = int((~valid).sum())
    out = out[valid].reset_index(drop=True)

    counts = out["label"].value_counts()
    total = len(out)
    stats = {
        "total": total,
        "horizon_bars": horizon,
        "tau_mode": mode,
        "target_mode": MODEL_TARGET_MODE,
        "price_col": str(out["future_ret_price"].iloc[0]) if total else "n/a",
        "buy": int(counts.get(LABEL_BUY, 0)),
        "hold": int(counts.get(LABEL_HOLD, 0)),
        "sell": int(counts.get(LABEL_SELL, 0)),
        "buy_pct": round(counts.get(LABEL_BUY, 0) / total * 100, 1) if total else 0,
        "hold_pct": round(counts.get(LABEL_HOLD, 0) / total * 100, 1) if total else 0,
        "sell_pct": round(counts.get(LABEL_SELL, 0) / total * 100, 1) if total else 0,
        "tau_media_pct": round(float(out["tau"].mean()) * 100, 3) if total else 0,
        "dropped_tail": dropped,
    }

    logger.info(
        "Labels: %d filas | BUY %d (%.1f%%) | HOLD %d (%.1f%%) | SELL %d (%.1f%%) | tau medio %.3f%%",
        total, stats["buy"], stats["buy_pct"], stats["hold"], stats["hold_pct"],
        stats["sell"], stats["sell_pct"], stats["tau_media_pct"],
    )
    return out, stats


def suggest_tau_grid(df: pd.DataFrame,
                     horizons: Optional[list] = None,
                     taus_pct: Optional[list] = None) -> pd.DataFrame:
    """
    Grid de sensibilidad tau x horizonte para elegir el target que deje
    HOLD entre 40-60%. Ver PROYECTO_NVDA.md seccion target.
    """
    horizons = horizons or [1, 3, 7]
    taus_pct = taus_pct or [0.3, 0.5, 0.8, 1.0, 1.2, 1.5]
    if "adj_close" in df.columns:
        price_col = "adj_close"
    elif "Adj Close" in df.columns:
        price_col = "Adj Close"
    else:
        price_col = "Close"
    px = df[price_col]
    if "date_ny" not in df.columns:
        dates = pd.to_datetime(df["Datetime"], utc=True).dt.tz_convert(
            "America/New_York"
        ).dt.date
    else:
        dates = df["date_ny"]
    rows = []
    for h in horizons:
        future_ret = px.groupby(dates, sort=False).shift(-h) / px - 1
        for tau in taus_pct:
            t = tau / 100.0
            labels = np.select(
                [future_ret > t, future_ret < -t], [LABEL_BUY, LABEL_SELL], default=LABEL_HOLD
            )
            valid = pd.Series(labels)[future_ret.notna()]
            total = len(valid)
            if total == 0:
                continue
            rows.append({
                "horizon_bars": h,
                "tau_pct": tau,
                "buy_pct": round((valid == LABEL_BUY).mean() * 100, 1),
                "hold_pct": round((valid == LABEL_HOLD).mean() * 100, 1),
                "sell_pct": round((valid == LABEL_SELL).mean() * 100, 1),
            })
    return pd.DataFrame(rows)


def save_labeled(df: pd.DataFrame) -> None:
    df.to_csv(LABELED_CSV, index=False)
    logger.info(f"Dataset etiquetado guardado: {LABELED_CSV} ({len(df)} filas)")


def build_labels(include_patterns: bool = True) -> Optional[pd.DataFrame]:
    """Carga features, aplica labels y guarda. Punto de entrada del pipeline."""
    if not FEATURES_CSV.exists():
        logger.error(f"No existe {FEATURES_CSV}. Corre primero feature_engineering.build_features()")
        return None

    df = pd.read_csv(FEATURES_CSV)
    df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
    labeled, stats = compute_labels(df)
    save_labeled(labeled)
    logger.info(f"Resumen: {stats}")

    grid = suggest_tau_grid(df)
    if not grid.empty:
        logger.info("Grid tau x horizonte (para elegir target):\n%s", grid.to_string(index=False))
    return labeled


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    result = build_labels()
    if result is not None:
        print(result[["Datetime", "Close", "future_ret", "tau", "label"]].tail(5).to_string(index=False))
