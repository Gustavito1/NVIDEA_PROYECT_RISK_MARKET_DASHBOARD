"""
Persistencia incremental del ANALISIS en PostgreSQL — SOLO tablas analytics.*.

Alcance de escritura (unico del proyecto entero):
    analytics.etl_watermark, fact_market_features, fact_bar_labels,
    fact_news_features, fact_news_impact, fact_model_registry,
    fact_model_outputs, fact_backtest_timeseries, fact_model_metrics,
    fact_confusion_matrix, daily_summary

NUNCA se escribe en las tablas del ETL del companero (public.ohlcv, public.news,
topics, news_topics, etl_metadata): son de solo lectura para este proyecto.

DESTINO DE ESCRITURA: configurado por DB_WRITE_* (por defecto el Postgres
local del docker-compose). Los DB_* de lectura (Aiven) no se tocan: el
mercado/noticias se LEEN de Aiven y el analisis se ESCRIBE donde diga
DB_WRITE_*. Si DB_WRITE_* no esta definido, se escribe en el mismo origen
de lectura.

Todas las sentencias son parametrizadas. Los UPSERTS solo revisan filas que
este mismo proceso calculo antes (necesario para fact_bar_labels, cuyo label
cambia hasta que llegan las H barras futuras).
"""
import logging
import os
from typing import Iterable, Optional

import pandas as pd

import core.config  # noqa: F401 — garantiza la carga de .env (DB_*)

logger = logging.getLogger(__name__)

# Las 11 tablas del analisis (schema analytics). El resto de la DB es
# intocable por diseno.
ANALYSIS_TABLES = (
    "etl_watermark", "fact_market_features", "fact_bar_labels",
    "fact_news_features", "fact_news_impact", "fact_model_registry",
    "fact_model_outputs", "fact_backtest_timeseries", "fact_model_metrics",
    "fact_confusion_matrix", "daily_summary",
)
SCHEMA = "analytics"


class SchemaNotReady(RuntimeError):
    """Faltan tablas del analisis: hay que ejecutar db/create-analitycs-db.sql."""


def _get_write_connection():
    """
    Conexion psycopg2 para escritura SOLO en tablas analytics.*.

    Destino: DB_WRITE_HOST/PORT/NAME/USER/PASSWORD/SSL (default: Postgres
    local del docker-compose). Sin DB_WRITE_*, cae en los DB_* de lectura.
    """
    import psycopg2

    g = os.getenv
    logger.debug("Conectando con PostgreSQL (escritura, tablas analytics.*)...")
    return psycopg2.connect(
        database=g("DB_WRITE_NAME") or g("DB_NAME"),
        user=g("DB_WRITE_USER") or g("DB_USER"),
        password=g("DB_WRITE_PASSWORD") or g("DB_PASSWORD"),
        host=g("DB_WRITE_HOST") or g("DB_HOST"),
        port=g("DB_WRITE_PORT") or g("DB_PORT"),
        sslmode=(g("DB_WRITE_SSL") or g("DB_SSL") or "require"),
        connect_timeout=int(g("DB_CONNECT_TIMEOUT", "10")),
    )


def check_schema() -> None:
    """Lanza SchemaNotReady si falta alguna tabla del analisis."""
    conn = None
    try:
        conn = _get_write_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = ANY(%s)",
                (SCHEMA, list(ANALYSIS_TABLES)),
            )
            presentes = {r[0] for r in cur.fetchall()}
        conn.rollback()
    finally:
        if conn is not None:
            conn.close()
    faltan = [t for t in ANALYSIS_TABLES if t not in presentes]
    if faltan:
        raise SchemaNotReady(
            f"Faltan tablas del analisis en el schema '{SCHEMA}': {faltan}. "
            "Crealas con: db/create-analitycs-db.sql (docker initdb) o "
            "psql ... -f scripts/db/schema_analytics.sql"
        )


def _py(value):
    """Convierte escalares numpy/pandas a tipos nativos de psycopg2."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if value is pd.NaT:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _upsert(conn, table: str, df: pd.DataFrame, conflict_cols: Iterable[str],
            update_cols: Optional[Iterable[str]] = None) -> int:
    """
    INSERT ... ON CONFLICT DO UPDATE sobre una tabla analytics.*.

    Lanza si la tabla no esta en ANALYSIS_TABLES (guarda de alcance).
    """
    if table not in ANALYSIS_TABLES:
        raise ValueError(f"Tabla fuera del alcance de escritura: {table}")
    if df is None or df.empty:
        return 0

    cols = list(df.columns)
    update = [c for c in (update_cols if update_cols is not None else cols)
              if c not in conflict_cols]
    col_sql = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    conflict = ", ".join(conflict_cols)
    if update:
        set_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update) + ", computed_at = NOW()"
    else:
        set_sql = "computed_at = NOW()"
    sql = (f"INSERT INTO {SCHEMA}.{table} ({col_sql}) VALUES ({ph}) "
           f"ON CONFLICT ({conflict}) DO UPDATE SET {set_sql}")

    rows = [tuple(_py(v) for v in row) for row in df.itertuples(index=False, name=None)]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def upsert_features(df: pd.DataFrame, symbol: str = "NVDA",
                    timeframe: str = "intraday") -> int:
    """Upsert de features por barra (df en formato pipeline, con Datetime)."""
    if df is None or df.empty:
        return 0
    out = df.copy().rename(columns={"Datetime": "ts"})
    if "date_ny" not in out.columns:
        out["date_ny"] = pd.to_datetime(out["ts"], utc=True).dt.tz_convert(
            "America/New_York").dt.date
    out["symbol"] = symbol
    out["timeframe"] = timeframe

    conn = None
    try:
        conn = _get_write_connection()
        n = _upsert(conn, "fact_market_features", out,
                    conflict_cols=("symbol", "timeframe", "ts"))
        conn.commit()
        logger.info(f"fact_market_features: {n} filas upsert ({symbol})")
        return n
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def upsert_labels(df: pd.DataFrame) -> int:
    """
    Upsert de labels por barra (revisable: la cola se re-etiqueta cuando
    llegan las H barras futuras).
    """
    if df is None or df.empty:
        return 0
    label_cols = ["future_ret", "future_ret_mode", "future_ret_price", "tau",
                  "label", "label_start", "label_end", "label_horizon_bars"]
    out = df.copy().rename(columns={"Datetime": "ts"})
    cols = ["ts"] + [c for c in label_cols if c in out.columns]
    out = out[cols]

    conn = None
    try:
        conn = _get_write_connection()
        n = _upsert(conn, "fact_bar_labels", out, conflict_cols=("ts",))
        conn.commit()
        logger.info(f"fact_bar_labels: {n} filas upsert")
        return n
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def get_watermark(table: str, column: str = "ts",
                  symbol: str = "NVDA") -> Optional[pd.Timestamp]:
    """Ultima barra persistida en una tabla del analisis (None si vacia)."""
    if table not in ANALYSIS_TABLES:
        raise ValueError(f"Tabla fuera del alcance: {table}")
    conn = None
    try:
        conn = _get_write_connection()
        with conn.cursor() as cur:
            if table == "fact_market_features":
                cur.execute(
                    f"SELECT MAX({column}) FROM {SCHEMA}.{table} WHERE symbol = %s",
                    (symbol,),
                )
            else:
                cur.execute(f"SELECT MAX({column}) FROM {SCHEMA}.{table}")
            row = cur.fetchone()
        conn.rollback()
        if row is None or row[0] is None:
            return None
        return pd.to_datetime(row[0], utc=True)
    finally:
        if conn is not None:
            conn.close()


def upsert_frame(table: str, df: pd.DataFrame, conflict_cols: Iterable[str]) -> int:
    """Upsert generico sobre una tabla analytics.* (guarda de alcance incluida)."""
    conn = None
    try:
        conn = _get_write_connection()
        n = _upsert(conn, table, df, conflict_cols)
        conn.commit()
        logger.info(f"{table}: {n} filas upsert")
        return n
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def query_df(sql: str, params=()) -> Optional[pd.DataFrame]:
    """SELECT contra el destino de analisis (DB_WRITE_*). None si falla."""
    conn = None
    try:
        conn = _get_write_connection()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
        conn.rollback()
        return pd.DataFrame.from_records(rows, columns=cols)
    except Exception as e:
        logger.error(f"Error consultando tablas de analisis: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def update_watermark(pipeline_name: str, symbol: str = "NVDA",
                     last_ohlcv_ts=None, last_news_ts=None) -> None:
    """Registra el progreso incremental en analytics.etl_watermark."""
    conn = None
    try:
        conn = _get_write_connection()
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.etl_watermark "
                "(pipeline_name, symbol, last_ohlcv_ts, last_news_ts, updated_at) "
                "VALUES (%s, %s, %s, %s, NOW()) "
                "ON CONFLICT (pipeline_name, symbol) DO UPDATE SET "
                "last_ohlcv_ts = COALESCE(EXCLUDED.last_ohlcv_ts, "
                f"{SCHEMA}.etl_watermark.last_ohlcv_ts), "
                "last_news_ts = COALESCE(EXCLUDED.last_news_ts, "
                f"{SCHEMA}.etl_watermark.last_news_ts), "
                "updated_at = NOW()",
                (pipeline_name, symbol, last_ohlcv_ts, last_news_ts),
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"No se pudo actualizar etl_watermark: {e}")
        if conn is not None:
            conn.rollback()
    finally:
        if conn is not None:
            conn.close()
