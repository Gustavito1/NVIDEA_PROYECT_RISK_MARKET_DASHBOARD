"""
Calculo INCREMENTAL de features/labels con persistencia en la DB.

Filosofia: los rolling (SMA, beta, sentimiento...) solo miran al pasado, asi
que una ventana movil con suficiente warmup produce EXACTAMENTE los mismos
valores que recalcular todo el historico. Solo se recalcula la cola:

    watermark  = MAX(ts) persistido en analytics.fact_market_features
    ventana    = [watermark - WARMUP_DAYS, ultimo dato]   (calculo + contexto)
    escritura  = [watermark - REVISION_DAYS, ultimo dato] (upsert)

WARMUP_DAYS cubre el lookback maximo (sentimiento 30d, beta 60b, SMA 50b)
y la convergencia de los indicadores ewm (MACD/RSI/ATR). REVISION_DAYS cubre
las revisiones legitimas de la cola: labels pendientes (la barra t cambia de
label hasta que llegan las H barras futuras) y backfills recientes del ETL
de noticias.

Tablas escritas (unicas del proyecto): analytics.fact_market_features,
analytics.fact_bar_labels, analytics.etl_watermark. El resto de la DB es de
solo lectura.

Uso:
    python -m core.incremental            # incremental (rapido)
    python -m core.incremental --backfill # primera carga del historico completo
    python -m core.incremental --check    # estado sin escribir nada
"""
import argparse
import logging
import time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")

WARMUP_DAYS = 90     # lookback maximo (sentimiento 30d) + convergencia de los
                     # indicadores ewm (MACD/RSI/ATR: memoria exponencial;
                     # con 90 dias el residuo cae bajo el ruido float <1e-9)
REVISION_DAYS = 2    # cola re-escrita en cada ejecucion


def _load_window(window_start: Optional[pd.Timestamp]):
    """
    Carga NVDA+SOXQ y las recorta a la ventana.

    Con DB activa pide directamente desde window_start (menos trafico de red);
    si no, carga por completo y recorta.
    """
    from core.config import DATA_SOURCE
    from core.feature_engineering import load_intradia_sheets

    if DATA_SOURCE in ("db", "auto") and window_start is not None:
        try:
            from core.data_providers.db_source import load_ohlcv_sheet
            inicio = window_start.strftime("%Y-%m-%d %H:%M:%S%z")
            nvda = load_ohlcv_sheet("NVDA_1H", start_date=inicio)
            soxq = load_ohlcv_sheet("SOXQ_1H", start_date=inicio)
            if nvda is not None and not nvda.empty and soxq is not None and not soxq.empty:
                return nvda, soxq
        except Exception as e:
            logger.warning(f"Ventana directa desde DB no disponible ({e}) - carga completa")

    hojas = load_intradia_sheets(("NVDA_1H", "SOXQ_1H"))
    nvda, soxq = hojas.get("NVDA_1H"), hojas.get("SOXQ_1H")
    if nvda is None or nvda.empty:
        raise RuntimeError("Sin datos intradia NVDA: imposible calcular")
    if window_start is not None:
        nvda = nvda[nvda["Datetime"] >= window_start].reset_index(drop=True)
        if soxq is not None and not soxq.empty:
            soxq = soxq[soxq["Datetime"] >= window_start].reset_index(drop=True)
    return nvda, soxq


def incremental_update(full_backfill: bool = False) -> Optional[pd.DataFrame]:
    """
    Calcula y persiste la cola incremental de features y labels.

    Returns:
        DataFrame de features calculadas (util para reutilizar en prediccion),
        o None si fallo.
    """
    from core.data_providers import db_sink
    from core.feature_engineering import _drop_warmup, assemble_features
    from core.labels import compute_labels

    t0 = time.time()
    db_sink.check_schema()

    wm = db_sink.get_watermark("fact_market_features")
    backfill = full_backfill or wm is None
    if backfill:
        window_start = None
        write_start = None
        logger.info("BACKFILL: calculo del historico completo")
    else:
        window_start = wm - pd.Timedelta(days=WARMUP_DAYS)
        write_start = wm - pd.Timedelta(days=REVISION_DAYS)
        logger.info(f"INCREMENTAL desde {window_start} (watermark {wm}); "
                    f"se escribe desde {write_start}")

    nvda, soxq = _load_window(window_start)
    logger.info(f"Ventana: NVDA {len(nvda)} barras | SOXQ {0 if soxq is None else len(soxq)}")

    df = assemble_features(nvda, soxq)
    if backfill:
        df = _drop_warmup(df)

    # Filas a persistir (en backfill, todas; en incremental, la cola)
    escribir = df if write_start is None else df[df["Datetime"] >= write_start]

    # Labels sobre el marco completo de la ventana (el futuro intra-ventana
    # existe para todas las filas salvo las ultimas H, que quedaran pendientes
    # para la proxima ejecucion).
    labeled, stats = compute_labels(df)
    escribir_labels = labeled if write_start is None \
        else labeled[labeled["Datetime"] >= write_start]

    # Features tecnicas de SOXQ (misma tabla, symbol=SOXQ)
    from core.feature_engineering import compute_technical_features
    soxq_feats = compute_technical_features(soxq) if soxq is not None and not soxq.empty else None
    if soxq_feats is not None and write_start is not None:
        soxq_feats = soxq_feats[soxq_feats["Datetime"] >= write_start]

    n_feat = db_sink.upsert_features(escribir, symbol="NVDA")
    n_soxq = db_sink.upsert_features(soxq_feats, symbol="SOXQ") if soxq_feats is not None else 0
    n_lab = db_sink.upsert_labels(escribir_labels)

    # Fase 3: agregados diarios (news por dia + resumen de sesion "hoy vs ayer")
    desde = None if backfill else (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3))
    update_news_features(since=desde)
    update_news_impact(since=desde)
    update_daily_summary(since=desde)

    dt = time.time() - t0
    logger.info(
        f"INCREMENTAL OK en {dt:.1f}s: fact_market_features {n_feat}+{n_soxq} filas, "
        f"fact_bar_labels {n_lab} filas | labels ventana: {stats}"
    )
    ultimo = df["Datetime"].max() if len(df) else None
    db_sink.update_watermark("nvda_incremental", "NVDA", last_ohlcv_ts=ultimo)
    db_sink.update_watermark("nvda_incremental", "SOXQ", last_ohlcv_ts=ultimo)
    return df


def check_state() -> None:
    """Diagnostico: estado de las tablas del analisis SIN escribir."""
    from core.data_providers import db_sink

    db_sink.check_schema()
    for tabla in ("fact_market_features", "fact_bar_labels"):
        wm = db_sink.get_watermark(tabla, "ts")
        estado = f"watermark: {wm}" if wm is not None else "VACIA (falta backfill)"
        print(f"  {tabla:<24} {estado}")


# ============================================================
# FASE 3: agregados diarios (fact_news_features + daily_summary)
# ============================================================
def update_news_features(since: Optional[pd.Timestamp] = None) -> int:
    """
    Agrega noticias por dia (fact_news_features) desde Aiven (solo lectura)
    hacia la DB de analisis. `since` limita los dias recalculados; None = todo.
    """
    from core.data_providers import db_sink, db_source

    cutoff = since.strftime("%Y-%m-%d %H:%M:%S%z") if since is not None else "2000-01-01"
    agg = db_source._query_df("""
        SELECT day, COUNT(*)::int AS news_count,
               AVG(sentiment_score) AS avg_sentiment,
               AVG(relevance_score)  AS avg_relevance,
               AVG(sentiment_score) FILTER (WHERE has_tech) AS topic_technology_score
        FROM (
            SELECT n.id,
                   (n.time_published AT TIME ZONE 'America/New_York')::date AS day,
                   n.sentiment_score, n.relevance_score,
                   EXISTS (SELECT 1 FROM news_topics nt
                           WHERE nt.news_id = n.id AND nt.topic_code = 'technology') AS has_tech
            FROM news n
            WHERE n.time_published >= %s
        ) x
        GROUP BY day ORDER BY day
    """, (cutoff,))
    if agg is None or agg.empty:
        logger.warning("fact_news_features: sin agregados que persistir")
        return 0

    out = pd.DataFrame({
        "symbol": "NVDA",
        "day": agg["day"].astype(str),
        "news_count": agg["news_count"].astype(int),
        "avg_sentiment": agg["avg_sentiment"].astype(float),
        "avg_relevance": agg["avg_relevance"].astype(float),
        "topic_technology_score": agg["topic_technology_score"].astype(float),
    })
    n = db_sink.upsert_frame("fact_news_features", out, ("symbol", "day"))
    logger.info(f"fact_news_features: {n} dias agregados")

    # Marca de frescura de noticias en etl_watermark (antes quedaba NULL).
    ultimo = db_source._query_df("SELECT MAX(time_published) AS t FROM news")
    if ultimo is not None and not ultimo.empty and ultimo["t"].iloc[0] is not None:
        db_sink.update_watermark("nvda_incremental", "NVDA",
                                 last_news_ts=ultimo["t"].iloc[0])
    return n


def update_news_impact(since: Optional[pd.Timestamp] = None) -> int:
    """
    Impacto por articulo (fact_news_impact) sobre el conjunto COMPLETO de
    noticias (escritor unico: el informe ya no persiste esta tabla para no
    perder articulos deduplicados en visualizacion).
    """
    from core.data_providers import db_sink, db_source
    from core.news.alpha_vantage import add_impact_columns

    arts = db_source.load_news_articles()
    if arts is None or arts.empty:
        logger.warning("fact_news_impact: sin articulos que procesar")
        return 0
    arts = add_impact_columns(arts)
    if since is not None:
        arts = arts[arts["published_utc"] >= since]
    out = arts.dropna(subset=["news_id"]).copy()
    out["news_id"] = out["news_id"].astype("int64")
    out = out[["news_id", "polarity", "sentiment_label_es",
               "impact_score", "impact_level"]]
    n = db_sink.upsert_frame("fact_news_impact", out, ("news_id",))
    logger.info(f"fact_news_impact: {n} articulos")
    return n


def update_daily_summary(since: Optional[pd.Timestamp] = None) -> int:
    """
    Resume cada sesion (daily_summary) para el bloque "Hoy vs Ayer" del informe.

    Todo sale de las tablas de analisis (features, labels no directamente y
    model_outputs); `since` limita los dias recalculados (la cola diaria
    cambia mientras el dia esta en curso y cuando llegan las senales).
    """
    from core.data_providers import db_sink

    reg = db_sink.query_df(
        "SELECT model_version FROM analytics.fact_model_registry "
        "ORDER BY trained_at DESC LIMIT 1")
    mv = reg["model_version"].iloc[0] if reg is not None and not reg.empty else None

    nvda = db_sink.query_df(
        "SELECT ts, date_ny, open, close, atr_pct, rsi_14, sent_w1d, sent_w7d "
        "FROM analytics.fact_market_features WHERE symbol = 'NVDA' ORDER BY ts")
    if nvda is None or nvda.empty:
        logger.warning("daily_summary: sin features NVDA")
        return 0
    soxq = db_sink.query_df(
        "SELECT ts, date_ny, open, close "
        "FROM analytics.fact_market_features WHERE symbol = 'SOXQ' ORDER BY ts")
    outs = db_sink.query_df(
        "SELECT ts, signal, prob_buy, prob_hold, prob_sell "
        "FROM analytics.fact_model_outputs "
        "WHERE model_version = %s AND split IN ('oof','holdout') ORDER BY ts",
        (mv,)) if mv else None

    for frame in (nvda, soxq, outs):
        if frame is not None and not frame.empty:
            frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
            frame["dia"] = frame["ts"].dt.tz_convert(NY_TZ).dt.date

    g = nvda.groupby("dia", sort=True)
    day = pd.DataFrame({
        "date_ny": g.size().index,
        "n_bars": g.size().to_numpy(),
        "open": g["open"].first().to_numpy(dtype=float),
        "close": g["close"].last().to_numpy(dtype=float),
        "atr_pct": g["atr_pct"].last().to_numpy(dtype=float),
        "rsi_14": g["rsi_14"].last().to_numpy(dtype=float),
        "sent_avg_24h": g["sent_w1d"].last().to_numpy(dtype=float),
        "sent_avg_7d": g["sent_w7d"].last().to_numpy(dtype=float),
    })
    day["day_ret"] = day["close"] / day["open"] - 1.0

    if soxq is not None and not soxq.empty:
        gs = soxq.groupby("dia", sort=True)
        s = pd.DataFrame({"date_ny": gs.size().index,
                          "sox_open": gs["open"].first().to_numpy(dtype=float),
                          "sox_close": gs["close"].last().to_numpy(dtype=float)})
        s["soxq_day_ret"] = s["sox_close"] / s["sox_open"] - 1.0
        day = day.merge(s[["date_ny", "soxq_day_ret"]], on="date_ny", how="left")

    if outs is not None and not outs.empty:
        go = outs.groupby("dia", sort=True)
        counts = outs.pivot_table(index="dia", columns="signal",
                                  values="ts", aggfunc="count").fillna(0).astype(int)
        for col, name in [("BUY", "n_buy"), ("HOLD", "n_hold"), ("SELL", "n_sell")]:
            day[name] = day["date_ny"].map(counts.get(col, pd.Series(dtype=float))).fillna(0).astype(int)
        last = go.last().reset_index()
        day = day.merge(last[["dia", "signal", "prob_buy", "prob_hold", "prob_sell"]]
                        .rename(columns={"dia": "date_ny", "signal": "close_label",
                                         "prob_buy": "p_buy_close",
                                         "prob_hold": "p_hold_close",
                                         "prob_sell": "p_sell_close"}),
                        on="date_ny", how="left")

    if since is not None:
        day = day[day["date_ny"] >= since.date()]
    if day.empty:
        logger.info("daily_summary: sin dias que actualizar")
        return 0

    n = db_sink.upsert_frame("daily_summary", day, ("date_ny",))
    logger.info(f"daily_summary: {n} dias agregados")
    return n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from core.data_providers import db_sink

    parser = argparse.ArgumentParser(description="Pipeline incremental NVDA")
    parser.add_argument("--backfill", action="store_true",
                        help="Primera carga: historico completo")
    parser.add_argument("--check", action="store_true",
                        help="Estado de las tablas del analisis (sin escribir)")
    args = parser.parse_args()

    try:
        if args.check:
            check_state()
            return 0
        df = incremental_update(full_backfill=args.backfill)
        return 0 if df is not None else 1
    except db_sink.SchemaNotReady as e:
        logger.error(str(e))
        return 2
    except Exception as e:
        logger.error(f"Fallo del pipeline incremental: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
