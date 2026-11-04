"""
Fuente de datos: PostgreSQL remoto (Aiven) — SOLO LECTURA.

El compañero mantiene la DB con datos de IBKR (contenedor Docker con ibsync)
y noticias (esquema Alpha Vantage NEWS_SENTIMENT limpio a NVDA). Este modulo
solo ejecuta SELECT: la sesion se abre en modo read-only y no hay ni una sola
sentencia de escritura en todo el modulo.

Conexion (mismo patron que usa tu companero):
    .env              -> APP_PROFILE
    .env.{APP_PROFILE} -> DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_SSL

Tablas usadas:
    ohlcv(symbol, timeframe, date, open, high, low, close, volume, adj_close)
    news(title, url, time_published, summary, relevance_score,
         sentiment_score, sentiment_label, ...)

Salida en el formato que ya consume el pipeline:
    load_ohlcv_sheet()   -> Datetime, Open, High, Low, Close, Volume [, Adj Close]
    load_news_articles() -> published_utc, ticker, relevance_score, score, ...

Uso:
    python -m core.data_providers.db_source   # diagnostico de la DB
"""
import logging
import os
from typing import Optional

import pandas as pd

from core.config import DATA_START_DATE

logger = logging.getLogger(__name__)

OHLCV_TIMEFRAME = os.getenv("DB_OHLCV_TIMEFRAME", "intraday")
CONNECT_TIMEOUT_SEC = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))
STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT", "30000"))


def get_connection():
    """
    Abre una conexion psycopg2 en modo SOLO LECTURA.

    Misma interfaz que el codigo de tu companero; ademas fuerza
    `default_transaction_read_only` y `statement_timeout` para que ninguna
    consulta pueda escribir ni bloquear el pipeline indefinidamente.
    """
    import psycopg2  # lazy: el proyecto funciona sin psycopg2 (fallback CSV)

    logger.info("Conectando con PostgreSQL (solo lectura)...")
    connection = psycopg2.connect(
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        sslmode=os.getenv("DB_SSL") or "require",
        connect_timeout=CONNECT_TIMEOUT_SEC,
        options=("-c default_transaction_read_only=on "
                 f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"),
    )
    connection.set_session(readonly=True, autocommit=False)
    return connection


def ping() -> bool:
    """True si la DB responde a un SELECT trivial."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception as e:
        logger.debug(f"DB no disponible: {e}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _query_df(sql: str, params=()) -> Optional[pd.DataFrame]:
    """Ejecuta un SELECT y devuelve DataFrame (None si falla)."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
        conn.rollback()
        return pd.DataFrame.from_records(rows, columns=cols)
    except Exception as e:
        logger.error(f"Error consultando la DB: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ============================================================
# OHLCV (velas 1h NVDA / SOXQ)
# ============================================================
_SYMBOL_BY_SHEET = {"NVDA_1H": "NVDA", "SOXQ_1H": "SOXQ"}


def load_ohlcv_sheet(sheet: str,
                     start_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    Carga barras intradia desde la tabla `ohlcv` en el formato del pipeline
    (Datetime UTC + Open/High/Low/Close/Volume).

    - timeframe configurable via DB_OHLCV_TIMEFRAME (default 'intraday')
    - corte temporal DATA_START_DATE (SOXQ inicia 2021-06-11)
    - adj_close solo se expone si esta COMPLETO (evita empalmar series
      ajustadas con crudas y generar retornos falsos); si no, se usa Close.

    Devuelve None ante cualquier error (para que el fallback 'auto' funcione)
    y un DataFrame con el esquema del pipeline (posiblemente vacio) si la DB
    responde sin datos.
    """
    symbol = _SYMBOL_BY_SHEET.get(sheet, sheet.replace("_1H", ""))
    start = start_date or DATA_START_DATE

    sql = """
        SELECT date, open, high, low, close, volume, adj_close
        FROM ohlcv
        WHERE symbol = %s AND timeframe = %s AND date >= %s::date
        ORDER BY date
    """
    df = _query_df(sql, (symbol, OHLCV_TIMEFRAME, start))
    if df is None:
        return None

    cols = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
    if df.empty:
        logger.warning(f"DB: sin barras {symbol}/{OHLCV_TIMEFRAME} desde {start}")
        return pd.DataFrame(columns=cols)

    try:
        out = pd.DataFrame()
        out["Datetime"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
        for src, dst in [("open", "Open"), ("high", "High"), ("low", "Low"),
                         ("close", "Close"), ("volume", "Volume")]:
            out[dst] = pd.to_numeric(df[src], errors="coerce")
        out = out.dropna(subset=["Datetime", "Open", "High", "Low", "Close"])
        out["Volume"] = out["Volume"].fillna(0).astype("int64")

        adj = pd.to_numeric(df["adj_close"], errors="coerce")
        if len(adj) and adj.notna().all():
            out["Adj Close"] = adj.loc[out.index]
            logger.info(f"{sheet}: adj_close completo - se usa como precio base")
        else:
            logger.info(f"{sheet}: adj_close vacio/incompleto - se usa Close crudo")

        out = out.drop_duplicates(subset=["Datetime"], keep="last").reset_index(drop=True)
    except Exception as e:
        logger.error(f"Error transformando barras {sheet} de la DB: {e}")
        return None

    logger.info(f"{sheet}: {len(out)} barras desde la DB "
                f"({out['Datetime'].min()} -> {out['Datetime'].max()})")
    return out


# ============================================================
# NOTICIAS (esquema NEWS_SENTIMENT limpio a NVDA)
# ============================================================
def load_news_articles() -> Optional[pd.DataFrame]:
    """
    Carga noticias desde la tabla `news` en el formato normalizado que ya
    consume core/news/alpha_vantage.py:

        published_utc, ticker, relevance_score, score, label, title, url

    La tabla no guarda ticker (la limpieza de tu companero deja solo NVDA),
    por lo que se fija ticker='NVDA'. score = sentiment_score de la fila.

    Devuelve None ante cualquier error (fallback 'auto') y un DataFrame con el
    esquema normalizado (posiblemente vacio) si la DB responde sin articulos.
    """
    sql = """
        SELECT id, title, url, time_published, summary, source,
               relevance_score, sentiment_score, sentiment_label
        FROM news
        ORDER BY time_published
    """
    df = _query_df(sql)
    if df is None:
        return None

    cols = ["news_id", "published_utc", "ticker", "relevance_score", "score",
            "label", "title", "url", "source"]
    if df.empty:
        logger.warning("DB: tabla news vacia")
        return pd.DataFrame(columns=cols)

    try:
        out = pd.DataFrame()
        out["news_id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
        # errors="coerce": una fila con fecha corrupta no puede tumbar el paso
        # de features completo (ademas permite que el fallback 'auto' actue).
        out["published_utc"] = pd.to_datetime(df["time_published"], utc=True,
                                              errors="coerce")
        out["ticker"] = "NVDA"
        out["relevance_score"] = pd.to_numeric(df["relevance_score"], errors="coerce").fillna(1.0)
        out["score"] = pd.to_numeric(df["sentiment_score"], errors="coerce").fillna(0.0)
        out["label"] = df["sentiment_label"].fillna("")
        out["title"] = df["title"].fillna("")
        out["url"] = df["url"].fillna("")
        out["source"] = df["source"].fillna("")
        invalidas = int(out["published_utc"].isna().sum())
        if invalidas:
            logger.warning(f"DB: {invalidas} noticias con fecha invalida descartadas")
        out = out.dropna(subset=["published_utc"]).reset_index(drop=True)
    except Exception as e:
        logger.error(f"Error transformando noticias de la DB: {e}")
        return None

    logger.info(f"NOTICIAS: {len(out)} articulos desde la DB "
                f"({out['published_utc'].min()} -> {out['published_utc'].max()})")
    return out


def diagnostico() -> None:
    """Imprime un resumen de la DB (diagnostico manual)."""
    print("== ohlcv ==")
    resumen = _query_df(
        "SELECT symbol, timeframe, COUNT(*) AS barras, MIN(date) AS desde, MAX(date) AS hasta "
        "FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    )
    print(resumen.to_string(index=False) if resumen is not None else "sin conexion")

    print("\n== news ==")
    resumen = _query_df(
        "SELECT COUNT(*) AS noticias, MIN(time_published) AS desde, "
        "MAX(time_published) AS hasta, "
        "MIN(relevance_score) AS rel_min, AVG(relevance_score) AS rel_media, "
        "MAX(relevance_score) AS rel_max "
        "FROM news"
    )
    print(resumen.to_string(index=False) if resumen is not None else "sin conexion")

    print("\n== news x topics ==")
    resumen = _query_df(
        "SELECT t.code, COUNT(*) AS n FROM news_topics nt "
        "JOIN topics t ON t.code = nt.topic_code GROUP BY t.code ORDER BY n DESC"
    )
    print(resumen.to_string(index=False) if resumen is not None else "sin conexion")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    diagnostico()
