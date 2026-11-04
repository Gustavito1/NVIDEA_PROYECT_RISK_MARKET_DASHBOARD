-- ============================================================
-- PRECIARIO_NVDA - ESQUEMA ANALISIS FUSIONADO (recomendado)
-- Base: arquitectura del DBA (schema analytics, fact_*, model_version)
-- + lo minimo que exige el informe HTML y el pipeline ML para
-- funcionar 100% desde la DB (sin CSVs).
-- SUPERSEDE a scripts/db/schema_analysis.sql.
-- ============================================================
CREATE SCHEMA IF NOT EXISTS analytics;

-- 1) CONTROL INCREMENTAL (del DBA, sin cambios)
CREATE TABLE IF NOT EXISTS analytics.etl_watermark (
  pipeline_name TEXT NOT NULL,
  symbol VARCHAR(15) NOT NULL,
  last_ohlcv_ts TIMESTAMPTZ,
  last_news_ts TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (pipeline_name, symbol)
);

-- 2) FEATURES POR BARRA (del DBA, AMPLIADO de 14 a las 75 columnas
--    reales del pipeline: sin ellas no se puede entrenar/inferrer el
--    modelo ni pintar el HTML completo. NVDA y SOXQ comparten tabla
--    via symbol; SOXQ usa el subconjunto tecnico)
CREATE TABLE IF NOT EXISTS analytics.fact_market_features (
    symbol                   VARCHAR(15) NOT NULL DEFAULT 'NVDA',
    timeframe                TEXT NOT NULL DEFAULT 'intraday',
    ts                     TIMESTAMPTZ NOT NULL,
    Open                   DOUBLE PRECISION,
    High                   DOUBLE PRECISION,
    Low                    DOUBLE PRECISION,
    Close                  DOUBLE PRECISION,
    Volume                 BIGINT,
    adj_close              DOUBLE PRECISION,
    ret_1b                 DOUBLE PRECISION,
    log_ret_1b             DOUBLE PRECISION,
    ret_3b                 DOUBLE PRECISION,
    ret_1d                 DOUBLE PRECISION,
    sma_20                 DOUBLE PRECISION,
    dist_sma_20            DOUBLE PRECISION,
    sma_50                 DOUBLE PRECISION,
    dist_sma_50            DOUBLE PRECISION,
    macd                   DOUBLE PRECISION,
    macd_signal            DOUBLE PRECISION,
    macd_hist              DOUBLE PRECISION,
    macd_pct               DOUBLE PRECISION,
    macd_hist_pct          DOUBLE PRECISION,
    rsi_14                 DOUBLE PRECISION,
    bb_mid                 DOUBLE PRECISION,
    bb_pctb                DOUBLE PRECISION,
    bb_width               DOUBLE PRECISION,
    atr_14                 DOUBLE PRECISION,
    atr_pct                DOUBLE PRECISION,
    vol_zscore             DOUBLE PRECISION,
    volume_surge           INTEGER,
    hl_range_pct           DOUBLE PRECISION,
    close_pos              DOUBLE PRECISION,
    date_ny                DATE,
    hour_ny                INTEGER,
    minute_ny              INTEGER,
    day_of_week            INTEGER,
    bar_of_day             INTEGER,
    bars_left              INTEGER,
    is_first_bar           INTEGER,
    is_last_bar            INTEGER,
    vwap                   DOUBLE PRECISION,
    vwap_dist              DOUBLE PRECISION,
    orb_high               DOUBLE PRECISION,
    orb_low                DOUBLE PRECISION,
    dist_orb_high          DOUBLE PRECISION,
    dist_orb_low           DOUBLE PRECISION,
    orb_break_up           INTEGER,
    orb_break_down         INTEGER,
    cum_day_ret            DOUBLE PRECISION,
    gap_open_pct           DOUBLE PRECISION,
    sox_close              DOUBLE PRECISION,
    sox_volume             DOUBLE PRECISION,
    sox_ret_1b             DOUBLE PRECISION,
    sox_ret_1d             DOUBLE PRECISION,
    sox_ratio              DOUBLE PRECISION,
    sox_ratio_z            DOUBLE PRECISION,
    beta_60b               DOUBLE PRECISION,
    corr_60b               DOUBLE PRECISION,
    alpha_1b               DOUBLE PRECISION,
    alpha_5b               DOUBLE PRECISION,
    rel_strength_1d        DOUBLE PRECISION,
    sent_decay             DOUBLE PRECISION,
    hours_since_news       DOUBLE PRECISION,
    sent_w6h               DOUBLE PRECISION,
    cnt_w6h                INTEGER,
    sent_w1d               DOUBLE PRECISION,
    cnt_w1d                INTEGER,
    sent_w3d               DOUBLE PRECISION,
    cnt_w3d                INTEGER,
    sent_w7d               DOUBLE PRECISION,
    cnt_w7d                INTEGER,
    sent_w30d              DOUBLE PRECISION,
    cnt_w30d               INTEGER,
    sent_momentum          DOUBLE PRECISION,
    has_sentiment_24h      DOUBLE PRECISION,
    pat_bull_count         INTEGER,
    pat_bear_count         INTEGER,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS ix_market_symbol_tf_ts
  ON analytics.fact_market_features (symbol, timeframe, ts DESC);
CREATE INDEX IF NOT EXISTS ix_market_date
  ON analytics.fact_market_features (symbol, date_ny);

-- 3) LABELS POR BARRA (NUEVO - gap bloqueante del esquema del DBA:
--    sin esto no hay re-entrenamiento, IC, ni label_real/future_ret)
CREATE TABLE IF NOT EXISTS analytics.fact_bar_labels (
    ts                       TIMESTAMPTZ NOT NULL,
    future_ret             DOUBLE PRECISION,
    future_ret_mode        TEXT,
    future_ret_price       TEXT,
    tau                    DOUBLE PRECISION,
    label                  TEXT,
    label_start            TIMESTAMPTZ,
    label_end              TIMESTAMPTZ,
    label_horizon_bars     INTEGER,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts)
);

-- 4) NOTICIAS AGREGADAS POR DIA (del DBA, sin cambios; cache derivada
--    para el grafico barras + linea de doble eje Y)
CREATE TABLE IF NOT EXISTS analytics.fact_news_features (
    symbol VARCHAR(15) NOT NULL,
    day DATE NOT NULL,
    news_count INT NOT NULL,
    avg_sentiment NUMERIC(10,6),
    avg_relevance NUMERIC(10,6),
    topic_technology_score NUMERIC(10,6),
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, day)
);
CREATE INDEX IF NOT EXISTS ix_news_symbol_day
  ON analytics.fact_news_features (symbol, day DESC);

-- 5) IMPACTO POR ARTICULO (NUEVO - gap bloqueante: fact_news_features
--    es un agregado diario y NO puede reproducir las tablas/heatmap del
--    HTML ni las features de sentimiento point-in-time del modelo.
--    El texto crudo ya vive en public.news (Aiven); aqui solo lo derivado.
--    La FK es CONDICIONAL: en el Postgres local no existe public.news y la
--    integridad es logica; en Aiven se crea la FK automaticamente.
CREATE TABLE IF NOT EXISTS analytics.fact_news_impact (
    news_id                  BIGINT PRIMARY KEY,
    polarity                 TEXT,            -- Positiva | Neutral | Negativa
    sentiment_label_es       TEXT,
    impact_score             DOUBLE PRECISION,
    impact_level             TEXT,            -- Alto | Medio | Bajo
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'news')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'fk_news_impact_news') THEN
        ALTER TABLE analytics.fact_news_impact
            ADD CONSTRAINT fk_news_impact_news
            FOREIGN KEY (news_id) REFERENCES public.news(id);
    END IF;
END $$;

-- 6) REGISTRO DE MODELOS (NUEVO - feature_list ORDENADA, label_map,
--    parametros, sha256 del .pkl: necesario para servir el modelo desde DB)
CREATE TABLE IF NOT EXISTS analytics.fact_model_registry (
    model_version            TEXT computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY,
    model_name               TEXT NOT NULL,
    feature_set              TEXT NOT NULL,
    n_features               INTEGER,
    feature_list             JSONB NOT NULL,  -- orden exacto de las 48 features
    label_map                JSONB,
    params                   JSONB,           -- H, tau, umbral, purge/embargo, costes
    data_range_start         TIMESTAMPTZ,
    data_range_end           TIMESTAMPTZ,
    artifact_path            TEXT,
    artifact_sha256          TEXT,
    trained_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7) SALIDAS DEL MODELO POR BARRA (del DBA + split/fold/close/future_ret)
CREATE TABLE IF NOT EXISTS analytics.fact_model_outputs (
    symbol                   VARCHAR(15) NOT NULL,
    timeframe                TEXT NOT NULL,
    ts                       TIMESTAMPTZ NOT NULL,
    model_version            TEXT NOT NULL,
    split                    TEXT NOT NULL DEFAULT 'live', -- oof | holdout | live
    fold_id                  INTEGER,
    close                    DOUBLE PRECISION,
    risk_score               DOUBLE PRECISION,
    signal                   TEXT,            -- BUY/HOLD/SELL
    prob_buy                 DOUBLE PRECISION,
    prob_hold                DOUBLE PRECISION,
    prob_sell                DOUBLE PRECISION,
    y_true                   TEXT,
    future_ret               DOUBLE PRECISION,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, ts, model_version)
);
CREATE INDEX IF NOT EXISTS ix_model_outputs_symbol_tf_ts
  ON analytics.fact_model_outputs (symbol, timeframe, ts DESC);

-- 8) BACKTEST POR BARRA (del DBA + close/signal/cost_bps para los
--    escenarios del informe: long_only, long_short, buy_hold, raw x coste)
CREATE TABLE IF NOT EXISTS analytics.fact_backtest_timeseries (
    symbol                   VARCHAR(15) NOT NULL,
    timeframe                TEXT NOT NULL,
    ts                       TIMESTAMPTZ NOT NULL,
    model_version            TEXT NOT NULL,
    strategy                 TEXT NOT NULL,   -- long_only | long_short | buy_hold | raw
    cost_bps                 DOUBLE PRECISION NOT NULL DEFAULT 0,
    position                 SMALLINT,        -- -1, 0, 1
    close                    DOUBLE PRECISION,
    signal                   TEXT,
    bar_return               DOUBLE PRECISION,
    equity                   DOUBLE PRECISION,
    drawdown                 DOUBLE PRECISION,
    trade_cost               DOUBLE PRECISION,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, ts, model_version, strategy, cost_bps)
);
CREATE INDEX IF NOT EXISTS ix_backtest_symbol_tf_ts
  ON analytics.fact_backtest_timeseries (symbol, timeframe, ts DESC);

-- 9) METRICAS AGREGADAS (del DBA + dimension feature_set/strategy/cost_bps
--    y metricas por clase. strategy='model' guarda clasificacion pura;
--    el resto guarda KPIs financieros por escenario)
CREATE TABLE IF NOT EXISTS analytics.fact_model_metrics (
    symbol                   VARCHAR(15) NOT NULL,
    timeframe                TEXT NOT NULL,
    model_version            TEXT NOT NULL,
    split                    TEXT NOT NULL,   -- oof | holdout
    feature_set              TEXT NOT NULL DEFAULT 'D_nvda_sox_news',
    strategy                 TEXT NOT NULL DEFAULT 'model', -- model | long_only | long_short | buy_hold | raw
    cost_bps                 DOUBLE PRECISION NOT NULL DEFAULT 0,
    n_features               INTEGER,
    n_eval                   INTEGER,
    f1_macro                 DOUBLE PRECISION,
    roc_auc                  DOUBLE PRECISION,
    accuracy                 DOUBLE PRECISION,
    balanced_accuracy        DOUBLE PRECISION,
    auc_folds_mean           DOUBLE PRECISION,
    auc_folds_std            DOUBLE PRECISION,
    precision_buy            DOUBLE PRECISION,
    recall_buy               DOUBLE PRECISION,
    f1_buy                   DOUBLE PRECISION,
    precision_hold           DOUBLE PRECISION,
    recall_hold              DOUBLE PRECISION,
    f1_hold                  DOUBLE PRECISION,
    precision_sell           DOUBLE PRECISION,
    recall_sell              DOUBLE PRECISION,
    f1_sell                  DOUBLE PRECISION,
    sharpe                   DOUBLE PRECISION,
    sortino                  DOUBLE PRECISION,
    max_drawdown             DOUBLE PRECISION,
    calmar                   DOUBLE PRECISION,
    win_rate                 DOUBLE PRECISION,
    profit_factor            DOUBLE PRECISION,
    total_return             DOUBLE PRECISION,
    annualized_return        DOUBLE PRECISION,
    tstat                    DOUBLE PRECISION,
    avg_trade                DOUBLE PRECISION,
    total_costs              DOUBLE PRECISION,
    active_bars              INTEGER,
    trades                   INT,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, model_version, split, feature_set, strategy, cost_bps)
);

-- 10) MATRIZ DE CONFUSION (del DBA, sin cambios)
CREATE TABLE IF NOT EXISTS analytics.fact_confusion_matrix (
    symbol VARCHAR(15) NOT NULL,
    timeframe TEXT NOT NULL,
    model_version TEXT NOT NULL,
    split TEXT NOT NULL,
    actual_label TEXT NOT NULL,
    predicted_label TEXT NOT NULL,
    count_value INT NOT NULL,
    row_pct NUMERIC(10,6),
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, model_version, split, actual_label, predicted_label)
);

-- 11) RESUMEN DIARIO (NUEVO - "comparar hoy vs ayer" de un vistazo)
CREATE TABLE IF NOT EXISTS analytics.daily_summary (
    date_ny                  DATE PRIMARY KEY,
    open                     DOUBLE PRECISION,
    close                    DOUBLE PRECISION,
    day_ret                  DOUBLE PRECISION,
    soxq_day_ret             DOUBLE PRECISION,
    n_bars                   INTEGER,
    atr_pct                  DOUBLE PRECISION,
    rsi_14                   DOUBLE PRECISION,
    sent_avg_24h             DOUBLE PRECISION,
    sent_avg_7d              DOUBLE PRECISION,
    n_buy                    INTEGER,
    n_hold                   INTEGER,
    n_sell                   INTEGER,
    close_label              TEXT,
    p_buy_close              DOUBLE PRECISION,
    p_hold_close             DOUBLE PRECISION,
    p_sell_close             DOUBLE PRECISION,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
