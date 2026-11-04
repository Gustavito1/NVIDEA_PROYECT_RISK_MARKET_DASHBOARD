-- ============================================================
-- PRECIARIO_NVDA - Esquema de ANALISIS incremental
-- Productos del analisis (hoy CSV planos) -> PostgreSQL.
-- Generado desde las columnas reales de nvda_features.csv (75 cols)
-- y nvda_labeled.csv. Ejecutar con rol que tenga CREATE.
-- ============================================================

-- 1) FEATURES por barra (sustituye nvda_features_1h.csv y
--    soxq_features_1h.csv: una tabla con symbol; SOXQ usa el subconjunto)
CREATE TABLE IF NOT EXISTS bar_features (
    bar_time               TIMESTAMPTZ NOT NULL,
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
    symbol                   VARCHAR(15) NOT NULL DEFAULT 'NVDA',
    timeframe                TEXT NOT NULL DEFAULT 'intraday',
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, bar_time)
);
CREATE INDEX IF NOT EXISTS ix_bar_features_date ON bar_features (symbol, date_ny);

-- 2) LABELS por barra (la parte revisable de nvda_labeled_1h.csv).
--    Tabla aparte porque el label de la barra t cambia hasta que llegan
--    las H barras futuras -> upsert de la cola en cada ejecucion.
CREATE TABLE IF NOT EXISTS bar_labels (
    bar_time                 TIMESTAMPTZ NOT NULL,
    future_ret             DOUBLE PRECISION,
    future_ret_mode        TEXT,
    future_ret_price       TEXT,
    tau                    DOUBLE PRECISION,
    label                  TEXT,
    label_start            TIMESTAMPTZ,
    label_end              TIMESTAMPTZ,
    label_horizon_bars     INTEGER,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bar_time)
);

-- 3) IMPACTO DE NOTICIAS (sustituye news_impact.csv)
CREATE TABLE IF NOT EXISTS news_impact (
    news_id                  BIGINT PRIMARY KEY REFERENCES news(id),
    sentiment_label_es       TEXT,
    sentiment_score          DOUBLE PRECISION,
    relevance_score          DOUBLE PRECISION,
    impact_score             DOUBLE PRECISION,
    impact_level             TEXT,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4) EJECUCIONES DE MODELO (metadatos + metricas JSONB de metrics.json
--    y experiment_ab.json)
CREATE TABLE IF NOT EXISTS model_runs (
    run_id                   BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    model_name               TEXT NOT NULL,
    feature_set              TEXT NOT NULL,
    n_features               INTEGER,
    params                   JSONB,
    data_range_start         TIMESTAMPTZ,
    data_range_end           TIMESTAMPTZ,
    oof_metrics              JSONB,
    holdout_metrics          JSONB,
    artifact_path            TEXT,
    artifact_sha256          TEXT,
    trained_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5) PREDICCIONES por barra y run (sustituye predictions_holdout.csv;
--    guarda OOF, holdout y live -> comparar dias)
CREATE TABLE IF NOT EXISTS model_predictions (
    run_id                   BIGINT NOT NULL REFERENCES model_runs(run_id),
    split                    TEXT NOT NULL,   -- oof | holdout | live
    bar_time                 TIMESTAMPTZ NOT NULL,
    close                    DOUBLE PRECISION,
    label_real               TEXT,
    future_ret               DOUBLE PRECISION,
    p_buy                    DOUBLE PRECISION,
    p_hold                   DOUBLE PRECISION,
    p_sell                   DOUBLE PRECISION,
    signal                   TEXT,
    PRIMARY KEY (run_id, split, bar_time)
);

-- 6) BACKTEST por barra (sustituye backtest_holdout.csv; scenario guarda el
--    grid de costes: 'long_0.10pct', 'ls_0.02pct', 'buy_hold'...)
CREATE TABLE IF NOT EXISTS backtest_bars (
    run_id                   BIGINT NOT NULL REFERENCES model_runs(run_id),
    scenario                 TEXT NOT NULL,
    bar_time                 TIMESTAMPTZ NOT NULL,
    close                    DOUBLE PRECISION,
    signal                   TEXT,
    position_long            INTEGER,
    return_long              DOUBLE PRECISION,
    equity_long              DOUBLE PRECISION,
    position_ls              INTEGER,
    return_ls                DOUBLE PRECISION,
    equity_ls                DOUBLE PRECISION,
    buy_hold_equity          DOUBLE PRECISION,
    PRIMARY KEY (run_id, scenario, bar_time)
);

-- 7) RESUMEN DIARIO: "comparar hoy vs ayer" de un vistazo
CREATE TABLE IF NOT EXISTS daily_summary (
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

-- 8) CONTROL DE EJECUCIONES INCREMENTALES (misma forma que tu etl_metadata,
--    para monitorear junto al ETL de tu companero)
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                       BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    process_name             TEXT NOT NULL,   -- nvda_features | nvda_labels | nvda_report
    time_from                TIMESTAMPTZ,
    time_to                  TIMESTAMPTZ,
    started_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at              TIMESTAMPTZ,
    status                   TEXT,
    total_inserts            INTEGER,
    error_message            TEXT
);
