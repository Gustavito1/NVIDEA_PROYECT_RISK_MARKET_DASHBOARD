"""
Adaptador Alpha Vantage NEWS_SENTIMENT para DAY TRADING (barras 1h).

Dos modos de entrada:
  1. ARTICULO (recomendado): JSON crudo de Alpha o CSV normalizado con
     `published_utc, ticker, relevance_score, ticker_sentiment_score`.
     -> features point-in-time con ventanas moviles (6h, 24h, 3d, 7d, 30d).
        Para una barra en T solo se usan articulos con published_utc < T
        (regla estricta anti-leakage).
  2. AGREGADO DIARIO (fallback que hoy maneja tu companero): CSV con
     `date, sent_weighted, sent_count, bull_ratio`
     -> se aplica al dia siguiente (lag-1), nunca al mismo dia.

Los filtros daily/weekly/month del companero se cubren con las ventanas
moviles (24h, 168h, 720h) sin usar calendarios incompletos (sin leakage).
"""
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

NY_TZ = "America/New_York"

# Mapeo de labels Alpha Vantage (EN) a español para el informe y la DB.
SENTIMENT_LABEL_ES = {
    "Bullish": "Alcista",
    "Somewhat-Bullish": "Algo alcista",
    "Neutral": "Neutral",
    "Somewhat-Bearish": "Algo bajista",
    "Bearish": "Bajista",
}


def sentiment_label_es_from_score(score: float) -> str:
    """Label en español derivado del score continuo (mismos cortes que Alpha)."""
    if score >= 0.35:
        key = "Bullish"
    elif score >= 0.15:
        key = "Somewhat-Bullish"
    elif score > -0.15:
        key = "Neutral"
    elif score > -0.35:
        key = "Somewhat-Bearish"
    else:
        key = "Bearish"
    return SENTIMENT_LABEL_ES[key]


def add_impact_columns(arts: pd.DataFrame) -> pd.DataFrame:
    """
    Deriva las columnas de impacto por articulo (reglas unicas del informe/DB):

      polarity            Positiva | Neutral | Negativa  (score ± 0.15)
      impact_score        relevance_score * |score|
      impact_level        terciles de impact_score (Alto | Medio | Bajo)
      sentiment_label_es  label traducido al español (o derivado del score)

    Se calcula sobre el conjunto COMPLETO de articulos (antes de filtrar o
    deduplicar para visualizacion), asi la DB y el informe coinciden.
    """
    out = arts.copy()
    s_col = "score" if "score" in out.columns else "sentiment_score"
    score = pd.to_numeric(out[s_col], errors="coerce").fillna(0.0)
    rel = pd.to_numeric(out["relevance_score"], errors="coerce").fillna(1.0)

    out["polarity"] = np.where(score > 0.15, "Positiva",
                               np.where(score < -0.15, "Negativa", "Neutral"))
    out["impact_score"] = rel * score.abs()
    if len(out["impact_score"].dropna()) > 10:
        q_hi = out["impact_score"].quantile(0.667)
        q_lo = out["impact_score"].quantile(0.333)
        out["impact_level"] = np.where(out["impact_score"] >= q_hi, "Alto",
                                       np.where(out["impact_score"] >= q_lo, "Medio", "Bajo"))
    else:
        out["impact_level"] = "Bajo"

    etiquetas = out["label"].replace("", np.nan) if "label" in out.columns \
        else pd.Series(np.nan, index=out.index)
    out["sentiment_label_es"] = (
        etiquetas.map(SENTIMENT_LABEL_ES)
        .fillna(score.map(sentiment_label_es_from_score))
    )
    return out


def _parse_published(value: pd.Series) -> pd.Series:
    """Convierte timestamps Alpha Vantage o ISO a UTC."""
    text = value.astype(str).str.strip()
    compact = text.str.match(r"^\d{8}T\d{6}$")
    result = pd.Series(pd.NaT, index=value.index, dtype="datetime64[ns, UTC]")
    if compact.any():
        result.loc[compact] = pd.to_datetime(
            text.loc[compact], format="%Y%m%dT%H%M%S", utc=True, errors="coerce"
        )
    if (~compact).any():
        result.loc[~compact] = pd.to_datetime(
            text.loc[~compact], utc=True, errors="coerce"
        )
    return result


def load_articles(path: Path) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Carga noticias desde JSON Alpha o CSV normalizado.

    Returns:
        (articles, daily)
        articles: published_utc, ticker, relevance, score, label, title, url
        daily:    date, sent_weighted, sent_mean, sent_count, bull_ratio
        Devuelve (None, daily) si el archivo es una tabla diaria ya agregada.
    """
    path = Path(path)
    if not path.exists():
        logger.warning(f"No existe archivo de noticias: {path}")
        return None, None

    try:
        if path.suffix.lower() == ".json":
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            feed = payload.get("feed", payload if isinstance(payload, list) else [])
            rows = []
            for art in feed:
                published = art.get("time_published", "")
                title = art.get("title", "")
                url = art.get("url", "")
                for ts in art.get("ticker_sentiment", []):
                    rows.append({
                        "published_utc": published,
                        "ticker": ts.get("ticker", ""),
                        "relevance_score": ts.get("relevance_score", 0),
                        "ticker_sentiment_score": ts.get("ticker_sentiment_score", 0),
                        "label": ts.get("ticker_sentiment_label", ""),
                        "title": title,
                        "url": url,
                    })
            if not rows:
                logger.warning("JSON sin articulos parseables")
                return None, None
            df = pd.DataFrame(rows)
            df["published_utc"] = _parse_published(df["published_utc"])
            df = df.dropna(subset=["published_utc"])
            for c in ["relevance_score", "ticker_sentiment_score"]:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
            df["score"] = df["ticker_sentiment_score"]
            return df.sort_values("published_utc").reset_index(drop=True), None

        df = pd.read_csv(path)
        cols = set(df.columns)

        # CSV crudo de Alpha Vantage usado por el compañero.
        if "time_published" in cols and "published_utc" not in cols:
            df = df.rename(columns={"time_published": "published_utc"})
        if "sentiment_score" in cols and "score" not in cols:
            df["score"] = df["sentiment_score"]
        if "ticker_sentiment_score" in cols and "score" not in cols:
            df["score"] = df["ticker_sentiment_score"]

        if "published_utc" in df.columns and "score" in df.columns:
            df["published_utc"] = _parse_published(df["published_utc"])
            df = df.dropna(subset=["published_utc"])
            if "ticker" not in df.columns:
                df["ticker"] = "NVDA"
            if "relevance_score" not in df.columns:
                df["relevance_score"] = 1.0
            df["ticker"] = df["ticker"].astype(str)
            df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0)
            df["relevance_score"] = pd.to_numeric(
                df["relevance_score"], errors="coerce"
            ).fillna(1.0)
            return df.sort_values("published_utc").reset_index(drop=True), None

        if "date" in df.columns:
            return None, df

        logger.warning(f"Formato de noticias no reconocido: {list(df.columns)[:8]}")
        return None, None

    except Exception as e:
        logger.error(f"Error cargando noticias {path}: {e}")
        return None, None


def aggregate_daily(articles: pd.DataFrame) -> pd.DataFrame:
    """Agrega articulos a nivel diario (fecha NY)."""
    df = articles.copy()
    df["date"] = df["published_utc"].dt.tz_convert(NY_TZ).dt.date
    df["w"] = df["score"] * df["relevance_score"]
    df["positive"] = (df["score"] > 0.15).astype(float)

    grp = df.groupby("date", sort=True)
    daily = pd.DataFrame({
        "sum_w": grp["w"].sum(),
        "sum_rel": grp["relevance_score"].sum(),
        "sent_mean": grp["score"].mean(),
        "sent_count": grp["score"].size(),
        "bull_ratio": grp["positive"].mean(),
    }).reset_index()
    daily["sent_weighted"] = np.where(
        daily["sum_rel"] > 0, daily["sum_w"] / daily["sum_rel"], 0.0
    )
    return daily.drop(columns=["sum_w", "sum_rel"])


def _window_name(w: int) -> str:
    if w < 24:
        return f"sent_w{w}h"
    if w % 24 == 0:
        return f"sent_w{w // 24}d"
    return f"sent_w{w}h"


def rolling_sentiment_features(bars: pd.DataFrame, articles: pd.DataFrame,
                               ticker: str = "NVDA",
                               windows_h=(6, 24, 72, 168, 720),
                               half_life_h: float = 12.0,
                               relevance_min: float = 0.3) -> pd.DataFrame:
    """
    Features point-in-time: para cada barra T usa solo noticias < T.

    Columnas generadas:
        sent_w{H}h para cada ventana, cnt_{H}h, sent_decay, hours_since_news,
        sent_momentum, has_sentiment_24h
    """
    out = bars.copy().reset_index(drop=True)
    arts = articles.dropna(subset=["published_utc"]).copy()

    if ticker and "ticker" in arts.columns:
        arts = arts[arts["ticker"].str.upper() == ticker.upper()]
    arts = arts[arts["relevance_score"] >= relevance_min]
    arts = arts.sort_values("published_utc")

    if arts.empty:
        logger.warning("Sin articulos tras filtros - features de sentimiento en 0")
        return _empty_sentiment(out, windows_h)

    arts["w"] = arts["score"] * arts["relevance_score"]

    # Se usan timestamps de artículo, no buckets horarios. Una noticia de
    # 09:45 no puede entrar en una barra que comienza a las 09:30.
    times = arts["published_utc"].values.astype("datetime64[ns]")
    w_sum = arts["w"].to_numpy(dtype=float)
    rel_sum = arts["relevance_score"].to_numpy(dtype=float)
    counts = np.ones(len(arts), dtype=float)
    cum_w = np.concatenate([[0.0], np.cumsum(w_sum)])
    cum_rel = np.concatenate([[0.0], np.cumsum(rel_sum)])
    cum_cnt = np.concatenate([[0.0], np.cumsum(counts)])
    news_times = times

    bar_times = pd.to_datetime(out["Datetime"], utc=True).values.astype("datetime64[ns]")

    decay_den = np.zeros(len(out))
    decay_num = np.zeros(len(out))
    hours_since = np.full(len(out), np.nan)
    max_window = max(windows_h)

    for i, t in enumerate(bar_times):
        end = np.searchsorted(times, t, side="left")
        if end <= 0:
            continue
        last_idx = np.searchsorted(news_times, t, side="left") - 1
        if last_idx >= 0:
            hours_since[i] = (t - news_times[last_idx]) / np.timedelta64(1, "h")

        decay_start = np.searchsorted(
            news_times, t - np.timedelta64(max_window, "h"), side="left"
        )
        ages = (t - news_times[decay_start:end]) / np.timedelta64(1, "h")
        weights = np.exp(-ages / half_life_h)
        rel_w = rel_sum[decay_start:end] * weights
        num = float((w_sum[decay_start:end] * weights).sum())
        den = float(rel_w.sum())
        decay_den[i] = den
        decay_num[i] = num

    out["sent_decay"] = np.where(decay_den > 0, decay_num / np.maximum(decay_den, 1e-12), 0.0)
    out["hours_since_news"] = pd.Series(hours_since, index=out.index).fillna(999.0)

    for w in windows_h:
        col = _window_name(w)
        den = np.zeros(len(out))
        num = np.zeros(len(out))
        cnt = np.zeros(len(out), dtype=int)
        for i, t in enumerate(bar_times):
            end = np.searchsorted(times, t, side="left")
            if end <= 0:
                continue
            start = np.searchsorted(times, t - np.timedelta64(w, "h"), side="left")
            d = cum_rel[end] - cum_rel[start]
            n = cum_w[end] - cum_w[start]
            den[i] = d
            num[i] = n
            cnt[i] = int(cum_cnt[end] - cum_cnt[start])
        out[col] = np.where(den > 0, num / np.maximum(den, 1e-12), 0.0)
        out[f"cnt_{col.replace('sent_', '')}"] = cnt

    sent24 = out.get("sent_w1d", pd.Series(0.0, index=out.index))
    sent6 = out.get("sent_w6h", pd.Series(0.0, index=out.index))
    out["sent_momentum"] = sent6 - sent24
    cnt24 = out.get("cnt_w1d", pd.Series(0, index=out.index))
    out["has_sentiment_24h"] = (cnt24 > 0).astype(int)

    return out


def _empty_sentiment(out: pd.DataFrame, windows_h) -> pd.DataFrame:
    for w in windows_h:
        col = _window_name(w)
        out[col] = 0.0
        out[f"cnt_{col.replace('sent_', '')}"] = 0
    out["sent_decay"] = 0.0
    out["hours_since_news"] = 999.0
    out["sent_momentum"] = 0.0
    out["has_sentiment_24h"] = 0
    return out


def daily_lag_features(bars: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """
    Fallback: aplica el agregado diario del dia D-1 a las barras del dia D.
    Nunca usa el agregado del mismo dia (evita leakage de agregados).
    """
    out = bars.copy()
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d["date_use"] = (pd.to_datetime(d["date"]) + pd.Timedelta(days=1)).dt.date

    keep = ["date_use"] + [c for c in ["sent_weighted", "sent_mean", "sent_count", "bull_ratio"]
                           if c in d.columns]
    d = d[keep].rename(columns={
        "sent_weighted": "sent_prev_day",
        "sent_mean": "sent_mean_prev_day",
        "sent_count": "sent_count_prev_day",
        "bull_ratio": "bull_ratio_prev_day",
        "date_use": "date_ny",
    })

    out = out.merge(d, on="date_ny", how="left")
    for c in ["sent_prev_day", "sent_mean_prev_day", "bull_ratio_prev_day"]:
        if c in out.columns:
            out[c] = out[c].fillna(0.0)
    if "sent_count_prev_day" in out.columns:
        out["sent_count_prev_day"] = out["sent_count_prev_day"].fillna(0)
        out["has_sentiment_prev_day"] = (out["sent_count_prev_day"] > 0).astype(int)
    return out


def build_sentiment_features(bars: pd.DataFrame, path: Optional[Path] = None,
                             ticker: str = "NVDA") -> pd.DataFrame:
    """
    Punto de entrada: decide articulo-level (point-in-time) o diario (lag-1).

    Orden de fuentes:
      1. Si se pasa `path` explicito, manda el archivo.
      2. DB remota (Aiven, tabla news) segun DATA_SOURCE (db/auto).
      3. Archivos locales: data/news_nvda_articles.csv y news_nvda_daily.csv.
    """
    from core.config import (
        DATA_SOURCE,
        NEWS_ARTICLES_FILE,
        NEWS_DAILY_FILE,
        NEWS_HALF_LIFE_H,
        NEWS_WINDOWS_H,
        SENT_RELEVANCE_MIN,
    )

    def _from_articles(articles: pd.DataFrame, origen: str) -> pd.DataFrame:
        logger.info(f"Sentimiento point-in-time desde {origen} con {len(articles)} articulos")
        return rolling_sentiment_features(
            bars, articles, ticker=ticker,
            windows_h=NEWS_WINDOWS_H, half_life_h=NEWS_HALF_LIFE_H,
            relevance_min=SENT_RELEVANCE_MIN,
        )

    if path is None and DATA_SOURCE in ("db", "auto"):
        from core.data_providers.db_source import load_news_articles
        articles = load_news_articles()
        if articles is not None and not articles.empty:
            return _from_articles(articles, "la DB")
        if DATA_SOURCE == "db":
            logger.error("Noticias: DATA_SOURCE=db y la base de datos no devolvio articulos")
            return _empty_sentiment(bars.copy().reset_index(drop=True), NEWS_WINDOWS_H)
        logger.warning("Noticias: DB no disponible - fallback a archivos locales")

    if path is None:
        if NEWS_ARTICLES_FILE.exists():
            path = NEWS_ARTICLES_FILE
        elif NEWS_DAILY_FILE.exists():
            path = NEWS_DAILY_FILE

    if path is None:
        return _empty_sentiment(bars.copy().reset_index(drop=True), NEWS_WINDOWS_H)

    articles, daily = load_articles(path)
    if articles is not None and not articles.empty:
        return _from_articles(articles, path.name)
    if daily is not None and not daily.empty:
        logger.info(f"Sentimiento diario con lag-1 ({len(daily)} dias)")
        return daily_lag_features(bars, daily)

    logger.warning("Sin datos de sentimiento utilizables - features en 0")
    return _empty_sentiment(bars.copy().reset_index(drop=True), NEWS_WINDOWS_H)
