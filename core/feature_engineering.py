"""
Feature engineering INTRADIA (velas 1h) — DAY TRADING NVDA.

Genera el dataset de features a partir de:
  - core/output/intradia/nvda_intradia.xlsx (sheets NVDA_1H, SOXQ_1H)
  - sentimiento diario (opcional, Alpha Vantage via companero)

Salida:
  - core/output/features/nvda_features.csv

Ver INDICADORES.md seccion 8 para el detalle de cada variable.
"""
import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.config import (
    ATR_PERIOD,
    BB_PERIOD,
    BETA_WINDOW,
    DATA_START_DATE,
    FEATURES_CSV,
    INTRADAY_BARS_PER_DAY,
    INTRADIA_NVDA_CSV,
    INTRADIA_SOXQ_CSV,
    RSI_PERIOD,
    SMA_PERIODS,
)

logger = logging.getLogger(__name__)

NY_TZ = "America/New_York"


def _price_column(df: pd.DataFrame) -> str:
    """
    Columna de precio para features/target: Adj Close si existe (IBKR
    ADJUSTED_LAST),
    si no Close crudo (IBKR TRADES). Ver INDICADORES.md seccion 9.
    """
    return "Adj Close" if "Adj Close" in df.columns else "Close"


def _safe_div(a, b):
    a = a if isinstance(a, pd.Series) else pd.Series(a)
    b = b if isinstance(b, pd.Series) else pd.Series(b)
    return a.div(b).replace([np.inf, -np.inf], np.nan)


_EXPANDED_CSVS = {
    "NVDA_1H": INTRADIA_NVDA_CSV,
    "SOXQ_1H": INTRADIA_SOXQ_CSV,
}


def _load_expanded_csv(sheet: str) -> Optional[pd.DataFrame]:
    """
    Carga el CSV expandido por simbolo (historico 2020+, velas 1h) y aplica el
    corte temporal obligatorio (SOXQ inicia 2021-06-11, descarta pandemia).

    Devuelve None (y no lanza) si el CSV esta corrupto o ilegible, para que el
    resto de fuentes (DB / Excel) puedan actuar como fallback.
    """
    path = _EXPANDED_CSVS.get(sheet)
    if path is None or not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        df = df.rename(columns={"date": "Datetime", "open": "Open", "high": "High",
                                "low": "Low", "close": "Close", "volume": "Volume"})
        df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True, errors="coerce")
        df = df.dropna(subset=["Datetime"])
        cut = pd.Timestamp(DATA_START_DATE, tz="UTC")
        df = df[df["Datetime"] >= cut]
        cols = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
        df = df[cols].sort_values("Datetime").reset_index(drop=True)
    except Exception as e:
        logger.error(f"CSV expandido ilegible ({path.name}): {e} - se usan otras fuentes")
        return None
    logger.info(f"{sheet}: {len(df)} barras desde {DATA_START_DATE} ({path.name})")
    return df


def load_intradia_sheet(sheet: str) -> Optional[pd.DataFrame]:
    """
    Carga un sheet intradia segun DATA_SOURCE:
      - "db":   solo la DB remota (Aiven, solo lectura)
      - "auto": DB primero, fallback a CSV expandido / Excel IBKR (default)
      - "csv":  solo fuentes locales (legacy)
    """
    from core.config import DATA_SOURCE

    if DATA_SOURCE in ("db", "auto"):
        from core.data_providers.db_source import load_ohlcv_sheet
        db_df = load_ohlcv_sheet(sheet)
        if db_df is not None and not db_df.empty:
            return db_df
        if DATA_SOURCE == "db":
            logger.error(f"{sheet}: DATA_SOURCE=db y la base de datos no devolvio datos")
            return None
        logger.warning(f"{sheet}: DB no disponible - fallback a fuentes locales")

    expanded = _load_expanded_csv(sheet)
    if expanded is not None and not expanded.empty:
        return expanded

    from core.data_providers.ibkr_intraday import get_latest_intradia_data
    data = get_latest_intradia_data()
    df = data.get(sheet)
    if df is None or df.empty:
        return None
    df = df.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
    return df.sort_values("Datetime").reset_index(drop=True)


def load_intradia_sheets(sheets=("NVDA_1H", "SOXQ_1H")) -> Dict[str, Optional[pd.DataFrame]]:
    """
    Carga varias hojas garantizando la MISMA fuente para todas.

    Evita el error silencioso de NVDA desde la DB y SOXQ desde un CSV (o
    viceversa), que generaria deriva en beta_60b/sox_ratio_z por alineacion
    temporal distinta entre fuentes.
    """
    from core.config import DATA_SOURCE

    if DATA_SOURCE in ("db", "auto"):
        from core.data_providers.db_source import load_ohlcv_sheet
        db_data = {s: load_ohlcv_sheet(s) for s in sheets}
        if all(df is not None and not df.empty for df in db_data.values()):
            logger.info("Fuente efectiva de intradia: DB remota (todas las hojas)")
            return db_data
        if DATA_SOURCE == "db":
            logger.error("DATA_SOURCE=db y la DB no devolvio todas las hojas")
            return {s: None for s in sheets}
        logger.warning("DB incompleta o caida - fuentes LOCALES para todas las hojas")

    resultado: Dict[str, Optional[pd.DataFrame]] = {s: _load_expanded_csv(s) for s in sheets}
    if all(df is not None and not df.empty for df in resultado.values()):
        logger.info("Fuente efectiva de intradia: CSVs expandidos locales")
        return resultado

    from core.data_providers.ibkr_intraday import get_latest_intradia_data
    data = get_latest_intradia_data()
    for s in sheets:
        df = resultado.get(s)
        if df is None or df.empty:
            df = data.get(s)
            if df is not None and not df.empty:
                df = df.copy()
                df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
                df = df.sort_values("Datetime").reset_index(drop=True)
            resultado[s] = df
    return resultado


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Features tecnicas sobre barras (periodos en numero de barras).

    Usa Adj Close si esta disponible (retornos sin saltos por dividendo/split);
    si no, Close crudo. Deja la columna `adj_close` como referencia para labels.
    """
    out = df.copy()
    price_col = _price_column(out)
    out["adj_close"] = out[price_col].astype(float)
    close = out["adj_close"]

    out["ret_1b"] = close.pct_change()
    out["log_ret_1b"] = np.log(close / close.shift(1))
    out["ret_3b"] = close.pct_change(3)
    out["ret_1d"] = close.pct_change(INTRADAY_BARS_PER_DAY)

    for n in SMA_PERIODS:
        out[f"sma_{n}"] = close.rolling(n).mean()
        out[f"dist_sma_{n}"] = _safe_div(close - out[f"sma_{n}"], out[f"sma_{n}"])

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = macd - signal
    out["macd_pct"] = _safe_div(macd, close)
    out["macd_hist_pct"] = _safe_div(macd - signal, close)

    out["rsi_14"] = _rsi(close, RSI_PERIOD)

    bb_mid = close.rolling(BB_PERIOD).mean()
    bb_std = close.rolling(BB_PERIOD).std()
    bb_up = bb_mid + 2 * bb_std
    bb_lo = bb_mid - 2 * bb_std
    out["bb_mid"] = bb_mid
    out["bb_pctb"] = _safe_div(close - bb_lo, bb_up - bb_lo)
    out["bb_width"] = _safe_div(bb_up - bb_lo, bb_mid)

    prev_close = close.shift(1)
    tr = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - prev_close).abs(),
        (out["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr_14"] = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False, min_periods=ATR_PERIOD).mean()
    out["atr_pct"] = _safe_div(out["atr_14"], close)

    vol_mean = out["Volume"].rolling(20).mean()
    vol_std = out["Volume"].rolling(20).std()
    out["vol_zscore"] = _safe_div(out["Volume"] - vol_mean, vol_std)
    out["volume_surge"] = (out["Volume"] > 2 * vol_mean).astype(int)

    out["hl_range_pct"] = _safe_div(out["High"] - out["Low"], close)
    out["close_pos"] = _safe_div(out["Close"] - out["Low"], out["High"] - out["Low"]).fillna(0.5)

    return out


def compute_intraday_context(df: pd.DataFrame) -> pd.DataFrame:
    """Features de sesion: barra del dia, VWAP, opening range, gap, retorno acumulado."""
    out = df.copy()
    dt_ny = out["Datetime"].dt.tz_convert(NY_TZ)
    out["date_ny"] = dt_ny.dt.date
    out["hour_ny"] = dt_ny.dt.hour
    out["minute_ny"] = dt_ny.dt.minute
    out["day_of_week"] = dt_ny.dt.dayofweek

    grouped = out.groupby("date_ny", sort=False)
    out["bar_of_day"] = grouped.cumcount() + 1
    # No contar las filas futuras observadas de una sesion historica: en una
    # inferencia intradia la sesion esta incompleta y ese conteo filtraria
    # informacion del futuro. Los cierres anticipados se controlan luego por
    # calendario si se desea una feature especifica.
    bars_per_day = INTRADAY_BARS_PER_DAY
    out["bars_left"] = (bars_per_day - out["bar_of_day"]).clip(lower=0)
    out["is_first_bar"] = (out["bar_of_day"] == 1).astype(int)
    out["is_last_bar"] = (out["bar_of_day"] >= bars_per_day).astype(int)

    typical = (out["High"] + out["Low"] + out["Close"]) / 3
    pv = typical * out["Volume"]
    out["vwap"] = _safe_div(
        pv.groupby(out["date_ny"]).cumsum(),
        out["Volume"].groupby(out["date_ny"]).cumsum(),
    )
    out["vwap_dist"] = _safe_div(out["Close"] - out["vwap"], out["vwap"])

    out["orb_high"] = grouped["High"].transform("first")
    out["orb_low"] = grouped["Low"].transform("first")
    out["dist_orb_high"] = _safe_div(out["Close"] - out["orb_high"], out["orb_high"])
    out["dist_orb_low"] = _safe_div(out["Close"] - out["orb_low"], out["orb_low"])
    out["orb_break_up"] = (out["Close"] > out["orb_high"]).astype(int)
    out["orb_break_down"] = (out["Close"] < out["orb_low"]).astype(int)

    day_first_open = grouped["Open"].transform("first")
    out["cum_day_ret"] = _safe_div(out["Close"] - day_first_open, day_first_open)

    daily = out.groupby("date_ny", sort=False).agg(
        _day_first_open=("Open", "first"),
        _day_last_close=("Close", "last"),
    )
    daily["_prev_close"] = daily["_day_last_close"].shift(1)
    daily["gap_open_pct"] = _safe_div(
        daily["_day_first_open"] - daily["_prev_close"], daily["_prev_close"]
    )
    out = out.merge(
        daily[["gap_open_pct"]].reset_index(),
        on="date_ny", how="left",
    )
    return out


def compute_sox_features(nvda: pd.DataFrame, soxq: pd.DataFrame) -> pd.DataFrame:
    """Features relativas NVDA vs SOXQ (benchmark semiconductores)."""
    sox_price_col = _price_column(soxq)
    sox = soxq[["Datetime", sox_price_col, "Volume"]].rename(
        columns={sox_price_col: "sox_close", "Volume": "sox_volume"}
    )
    out = nvda.merge(sox, on="Datetime", how="left")
    out["sox_ret_1b"] = out["sox_close"].pct_change()
    out["sox_ret_1d"] = out["sox_close"].pct_change(INTRADAY_BARS_PER_DAY)

    out["sox_ratio"] = _safe_div(out["adj_close"], out["sox_close"])
    ratio_mean = out["sox_ratio"].rolling(BETA_WINDOW).mean()
    ratio_std = out["sox_ratio"].rolling(BETA_WINDOW).std()
    out["sox_ratio_z"] = _safe_div(out["sox_ratio"] - ratio_mean, ratio_std)

    cov = out["ret_1b"].rolling(BETA_WINDOW).cov(out["sox_ret_1b"])
    var = out["sox_ret_1b"].rolling(BETA_WINDOW).var()
    out["beta_60b"] = _safe_div(cov, var)
    out["corr_60b"] = out["ret_1b"].rolling(BETA_WINDOW).corr(out["sox_ret_1b"])
    out["alpha_1b"] = out["ret_1b"] - out["beta_60b"] * out["sox_ret_1b"]
    out["alpha_5b"] = out["alpha_1b"].rolling(5).mean()
    out["rel_strength_1d"] = out["ret_1d"] - out["sox_ret_1d"]

    return out


def add_sentiment_features(df: pd.DataFrame,
                           sentiment_df: Optional[pd.DataFrame] = None,
                           sentiment_path: Optional[Path] = None) -> pd.DataFrame:
    """
    Une sentimiento de noticias a las barras (sin leakage).

    Modos:
      1. sentiment_path o archivo por defecto (data/news_nvda_articles.csv /
         news_nvda_daily.csv): point-in-time si es articulo-level, lag-1 si es diario.
      2. sentiment_df: tabla diaria (date, sent_weighted, sent_count, bull_ratio)
         -> se aplica con lag-1 de forma automatica.

    Ver core/news/alpha_vantage.py y INDICADORES.md seccion 8.
    """
    from core.news.alpha_vantage import build_sentiment_features, daily_lag_features

    if sentiment_df is not None and not sentiment_df.empty:
        return daily_lag_features(df, sentiment_df)
    return build_sentiment_features(df, path=sentiment_path)


def add_pattern_features(df: pd.DataFrame, window: int = 50,
                         min_confidence: float = 0.30) -> pd.DataFrame:
    """
    Codifica los 57 patrones de PRECIARIO_OIL como features binarias.

    Optimizacion: los patrones de candlestick (1-3 velas) se detectan en UNA
    sola pasada sobre todo el dataframe; los de grafico/volumen (que solo
    terminan en la ultima barra de la ventana) se calculan rolling.

    Agrega columnas pat_<slug>, pat_bull_count, pat_bear_count y pat_conf_max.
    """
    from core.pattern_detector import PatternDetector

    out = df.copy().reset_index(drop=True)
    n = len(out)
    if n == 0:
        return out

    indexed = out.copy()
    indexed.index = pd.to_datetime(indexed["Datetime"], utc=True)

    flag_cols: Dict[str, np.ndarray] = {}
    bull_count = np.zeros(n, dtype=int)
    bear_count = np.zeros(n, dtype=int)
    conf_max = np.zeros(n, dtype=float)

    def _mark(i: int, p) -> None:
        slug = p.pattern.name.lower()
        if slug not in flag_cols:
            flag_cols[slug] = np.zeros(n, dtype=int)
        flag_cols[slug][i] = 1
        if p.signal.value == "bullish":
            bull_count[i] += 1
        elif p.signal.value == "bearish":
            bear_count[i] += 1
        conf_max[i] = max(conf_max[i], p.confidence)

    detector = PatternDetector(min_confidence=min_confidence)

    detector.patterns_found = []
    detector._detect_candlestick_patterns(indexed)
    for p in detector.patterns_found:
        if p.confidence >= min_confidence and 0 <= p.end_idx < n:
            _mark(int(p.end_idx), p)

    for i in range(window - 1, n):
        chunk = indexed.iloc[i - window + 1:i + 1]
        detector.patterns_found = []
        detector._detect_chart_patterns(chunk)
        detector._detect_volume_patterns(chunk)
        for p in detector.patterns_found:
            if p.confidence >= min_confidence and p.end_idx == len(chunk) - 1:
                _mark(i, p)

    for slug, arr in flag_cols.items():
        out[f"pat_{slug}"] = arr
    out["pat_bull_count"] = bull_count
    out["pat_bear_count"] = bear_count
    out["pat_conf_max"] = conf_max
    return out


def _drop_warmup(df: pd.DataFrame) -> pd.DataFrame:
    """
    Elimina el bloque INICIAL de warmup (rolling windows sin datos suficientes).

    Solo se descarta hasta la primera fila donde todas las columnas requeridas
    son validas. Las filas del MEDIO con NaN (gaps de datos, p.ej. SOXQ sin
    cubrir unas barras) se conservan: antes un dropna las borraba masivamente,
    perdia barras de entrenamiento y desalineaba los horizontes de labels.
    """
    required = ["sma_20", "rsi_14", "atr_14", "beta_60b", "sox_ratio_z"]
    subset = []
    for c in required:
        if c not in df.columns:
            continue
        if df[c].isna().all():
            logger.warning(f"'{c}' sin datos (warmup imposible) - no se usa como filtro")
            continue
        subset.append(c)
    if not subset or df.empty:
        return df

    first_valid = max(int(df[c].first_valid_index() or 0) for c in subset)
    out = df.iloc[first_valid:].reset_index(drop=True)
    logger.info(f"Warmup: {first_valid} filas iniciales descartadas ({len(out)} quedan)")

    intermedias = int(out[subset].isna().any(axis=1).sum())
    if intermedias:
        logger.warning(
            f"{intermedias} filas intermedias con NaN en {subset} (gaps de datos): "
            "se conservan; el entrenamiento las rellena con 0 (vigilar)"
        )
    return out


def build_features(sentiment_df: Optional[pd.DataFrame] = None,
                   include_patterns: bool = False,
                   save: bool = True) -> Optional[pd.DataFrame]:
    """Pipeline completo de features intradia."""
    hojas = load_intradia_sheets(("NVDA_1H", "SOXQ_1H"))
    nvda = hojas.get("NVDA_1H")
    soxq = hojas.get("SOXQ_1H")

    if nvda is None or nvda.empty:
        logger.error("No hay datos intradia NVDA. Corre primero core.data_providers.ibkr_intraday")
        return None

    logger.info(f"NVDA barras: {len(nvda)} | SOXQ barras: {0 if soxq is None else len(soxq)}")

    df = compute_technical_features(nvda)
    df = compute_intraday_context(df)

    if soxq is not None and not soxq.empty:
        df = compute_sox_features(df, soxq)
    else:
        logger.warning(
            "Sin datos SOXQ - features de sector quedaran NaN. "
            "Los modelos con features de sector (B/D) NO seran validos hasta cargar SOXQ."
        )
        for c in ["sox_ret_1b", "sox_ret_1d", "sox_ratio", "sox_ratio_z",
                  "beta_60b", "corr_60b", "alpha_1b", "alpha_5b", "rel_strength_1d"]:
            df[c] = np.nan

    df = add_sentiment_features(df, sentiment_df)

    if include_patterns:
        logger.info("Detectando patrones de velas (puede tardar unos segundos)...")
        df = add_pattern_features(df)
    else:
        df["pat_bull_count"] = 0
        df["pat_bear_count"] = 0

    df = _drop_warmup(df)

    if save:
        df.to_csv(FEATURES_CSV, index=False)
        logger.info(f"Features guardadas: {FEATURES_CSV} ({len(df)} filas, {len(df.columns)} columnas)")

    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    result = build_features()
    if result is not None:
        print(result.tail(3).to_string())
