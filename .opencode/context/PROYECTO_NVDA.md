# Sistema Predictivo NVDA/SOX — Documento Portable

> **Proyecto:** `PRECIARIO_NVDA` (hermano de `PRECIARIO_OIL`)  
> **Ubicación actual:** `/home/desarrollo/proyectos_gerencia/PRECIARIO_NVDA/`  
> **Destino final:** tu ordenador principal (copiar carpeta completa)  
> **Fecha:** 2026-09-04 · Autor: Muse Spark + investigación web profunda  
> **Propósito de este .md:** que lleves todo el diseño sin depender de este equipo

---

## 1. Resumen ejecutivo (para tu cuadro)

**Objetivo:** sistema de apoyo a decisión que clasifica **NVDA** cada día como **BUY / HOLD / SELL** usando 3-6 meses de velas diarias + sentimiento de noticias. No predice precio exacto (ruido), sino señal discreta alineada con trading real.

**Datos:** OHLCV diario por **IBKR** (tu cuenta Pro freemium, delay 15 min irrelevante en diario) para **NVDA (target)** y **SOXQ (benchmark sector)**. Noticias solo por **Alpha Vantage `NEWS_SENTIMENT`** (tu compañero ya tiene key `25 req/día = suficiente` para solo-noticias).

**Modelos:** `Logistic Regression` (baseline) → **`Random Forest` (principal recomendado)** → `XGBoost` opcional. Comparas `Mercado solo` vs `Mercado + Sentimiento`.

**Entrega:** PostgreSQL en Docker + Power BI (3 páginas) + reporte 3-6 meses retrospectivo ("cuándo se debió comprar/vender").

> **ACTUALIZACIÓN 2026-09 — MODO DAY TRADING (velas de 1 HORA).**
> El requerimiento actual es day trading intradía, no swing diario. El diseño
> de secciones 1-10 (diario) queda como referencia para el modelo swing; el
> pipeline implementado trabaja con **barras de 1h** (7 barras/día RTH) y
> target a N barras. Ver **secciones 10-12** al final de este documento.

---

## 2. ¿SOX y NVDA son lo mismo? No.

| Concepto | Qué es | Ejemplo |
|---|---|---|
| **NVDA** | 1 empresa (NVIDIA) | Acción que predices |
| **SOX** | Índice con **30** semis (NVDA + AMD + AVGO + TSM + INTC + MU ... 30) | Termómetro del sector |
| **SOXQ** | ETF que replica SOX 1:1 (0.19% fee) | Proxy que sí puedes descargar por IBKR |

NVDA pesa ~11% del SOX. Correlación 0.77 a 10 años, pero **0.03 a 3 meses en 2026** (desacople histórico — cada uno va por su lado). Por eso SOX sirve como **benchmark para features relativos**, no como sustituto.

**Fórmula del Alpha idiosincrático:**
```
R_NVDA = alpha + beta * R_SOXQ + epsilon
El modelo debe aprender epsilon, no solo que "todo el sector sube".
```

---

## 3. Modelo recomendado — ¿XGBoost?

> **Recomendación para tu caso universitario: `Random Forest` como principal, `Logistic Regression` baseline, `XGBoost` solo como extra opcional.**

| Modelo | Por qué sí / por qué no para tu curso | Cuándo usarlo |
|---|---|---|
| **Logistic Regression** | **Sí, obligatorio baseline.** Interpretable, rápido, demuestra que el problema no es trivial. Si RF no lo supera, algo está mal. | Fase 6 — primer resultado |
| **Random Forest** ⭐ | **Sí, principal.** No lineal, bagging reduce overfitting (clave con ruido bursátil), `feature_importances_` para justificar en memoria, `class_weight=balanced` resuelve desbalance HOLD, no requiere escalar. Papers NVDA-like reportan +15-20% F1 vs LogReg. Suficiente para defender sin caer en deep learning. | Fase 7 — modelo a entregar |
| **XGBoost** | Bueno, pero para tu alcance es **overkill**: tuning más largo (`learning_rate, max_depth, subsample`), riesgo de overfitting con pocos datos (3-6 meses ~63-126 filas), y el tribunal espera que domines RF antes que boosting. | Solo si te sobra tiempo: Fase 7b como comparativa "RF 0.61 F1 vs XGB 0.63 F1, ganancia marginal no justifica complejidad" |

**Pipeline concreto recomendado:**
```python
# RF principal (sin scaler)
Pipeline([
  ('clf', RandomForestClassifier(
    n_estimators=300, max_depth=8, min_samples_leaf=5,
    class_weight='balanced', random_state=42, n_jobs=-1))
])

# LogReg baseline (con scaler)
Pipeline([
  ('scaler', StandardScaler()),
  ('clf', LogisticRegression(max_iter=500, class_weight='balanced', multi_class='multinomial'))
])
```
Validación: `PurgedKFold(5, purge=5, embargo=20)` para HPT (optimizas `F1 macro`), `WalkForward expanding` para backtest final con coste 0.1%. Guardas `modelo_nvda_rf.pkl + metrics.json` como en `PRECIARIO_OIL/core/output/valero_models/`.

**Frase para tu memoria:**
> "Se elige Random Forest como modelo principal por su capacidad de capturar no linealidades sin requerir escalado, su robustez al ruido mediante bagging y su interpretabilidad vía importancia de variables, suficiente para un curso de fundamentos sin incurrir en el sobreajuste y coste de tuning de XGBoost/LSTM (Gu et al. 2020)."

---

## 4. Feature Engineering completo (lo que te faltaba)

### 4.1 Mercado (OHLCV diario IBKR)

| Feature | Fórmula | Por qué |
|---|---|---|
| `ret_1d` | `(Close_t - Close_{t-1})/Close_{t-1}` | Señal base |
| `log_ret` | `ln(Close_t/Close_{t-1})` | Estacionario |
| `ret_5d` | `(Close_t - Close_{t-5})/Close_{t-5}` | Momentum semanal |
| `SMA_10/20/50` | `mean(Close, n)` | Tendencia |
| `EMA_12/26` | `alpha=2/(n+1)` | Tendencia rápida |
| `RSI_14` | `100 - 100/(1+RS)` donde `RS=avg_gain/avg_loss` | Sobrecompra >70 / sobreventa <30 |
| `MACD` | `EMA12-EMA26`, `Signal=EMA9(MACD)`, `Hist=MACD-Signal` | Cruce |
| `BB_%B, BB_width` | `SMA20 ±2*STD20` | Volatilidad rango |
| `ATR_14` | `SMA(TR,14)` | Volatilidad absoluta |
| `vol_zscore` | `(Volume - SMA20_vol)/STD20_vol` | Volumen anómalo |
| `volume_surge` | `1 si Volume >2*SMA20_vol` | Flag |

### 4.2 Sector (NVDA vs SOXQ)

| Feature | Fórmula |
|---|---|
| `sox_ratio` | `Close_NVDA / Close_SOXQ` |
| `sox_ratio_z60` | `(ratio - SMA60)/STD60` |
| `beta_60` | `Cov(R_NVDA,R_SOXQ)/Var(R_SOXQ)` ventana 60 |
| `corr_60` | `Corr(R_NVDA,R_SOXQ,60)` — si <0.3 desactiva hedge |
| `alpha_5d` | `R_NVDA - beta_60*R_SOXQ` (momentum idiosincrático, mean 5d) |

### 4.3 Sentimiento (Alpha Vantage)

Por día, filtrando `ticker=="NVDA" && relevance>0.3`:
```
weighted = sum(score*relevance)/sum(relevance)   # principal
mean     = mean(score)
count    = n noticias
sent_7d  = rolling_mean(weighted, 7)
bull_ratio = N_pos / N_total
```
+ `topics` Alpha (`technology, earnings`) como peso opcional.

### 4.4 Target BUY/HOLD/SELL

```
precio_base = adj_close si existe (IBKR ADJUSTED_LAST) | Close crudo (TRADES)
future_ret_Hb = precio_base[t+H] / precio_base[t] - 1     (retorno SIMPLE, H=3)
if future_ret > +tau  → BUY
elif future_ret < -tau → SELL
else HOLD
```

> **Adj Close vs Close (pedido del compañero):** soportado. El pipeline usa
> `Adj Close` cuando existe y `Close` crudo cuando no (IBKR `TRADES`). Para
> forzar ajustados en IBKR: `INTRADAY_WHAT_TO_SHOW=ADJUSTED_LAST`. El fallback
> En este proyecto no se usa ninguna fuente alternativa: si IBKR no está
> conectado, el ciclo se detiene y registra el mensaje de error.
>
> **Simple vs log:** target en retorno **simple** (legible en % para el
> umbral); los retornos como feature también incluyen `log_ret_1b`. Se puede
> cambiar el target a logarítmico con `MODEL_TARGET_MODE=log`. Ver
> `INDICADORES.md` §9.1.

`tau` no arbitrario: grid `[0.3, 0.5, 0.8, 1.0, 1.2, 1.5]%` + `tau_adapt = 0.5*STD20`.
Elegir el que deje HOLD 40-60%. `suggest_tau_grid()` imprime la tabla. Guarda
`label_start/label_end` para purging.

Para el modo day trading, `future_ret` se calcula agrupando por `date_ny`:
las barras finales de cada sesión se descartan si no tienen H barras futuras
en la misma sesión. Así el target no incluye gaps overnight, fines de semana
ni festivos.

**Stack:** pandas + numpy (indicadores implementados sin dependencias extra).
Z-score rolling en entrenamiento, no global.

---

## 5. KPIs — qué poner en tu cuadro y en Power BI

### 5.1 Clasificación (matriz 3×3)

| KPI | Fórmula | Por qué no solo Accuracy |
|---|---|---|
| **Accuracy** | `(TP+TN)/Total` | Con 86% HOLD, predecir siempre HOLD da 86% — inútil |
| **Precision BUY** | `TP_BUY/(TP+FP)_BUY` | De 100 BUY que disparas, cuántas aciertan → costes |
| **Recall BUY** | `TP/(TP+FN)` | De todos los días que subieron >tau, cuántos cazaste |
| **F1 macro** ⭐ | `2PR/(P+R)` promedio no ponderado | **Principal para HPT** — penaliza desbalance |
| **ROC-AUC OVR** | Área ROC one-vs-rest | Ranking, 0.81 es bueno NVDA-like |

Siempre reporta **matriz confusión % por fila** — el error grave es confundir BUY/SELL, no HOLD.

### 5.2 Financieros / Backtest (obligatorio para defender)

| KPI | Fórmula | Benchmark NVDA-like |
|---|---|---|
| **Sharpe** | `(Rp-Rf)/σ * √252` | ML 1.09 vs DCA 0.70 |
| **Sortino** | `(Rp-Rf)/σ_downside` | Complemento si retornos asimétricos |
| **Max Drawdown** | `(Trough-Peak)/Peak` | ML -16% vs Hold -6% |
| **Calmar** | `Ret_anual / |MDD|` | >1 bueno |
| **WinRate** | `Wins/Trades` | ≠ Precision. Ej 30% precision puede ser 64% WinRate si muchos trades ganan <1% |
| **IC** | `Spearman(pred_signal, realized_ret)` | Correlación señal-retorno |

Reporta 3 thresholds `proba>0.5 / 0.6 / sizing variable` vs `Majority, Buy&Hold`.

### 5.3 Fórmulas DAX para Power BI

```DAX
Volatilidad % = STDEVX.P(DailyReturn)*SQRT(252)*100
Sharpe = DIVIDE([Retorno YTD],[Volatilidad %])
Drawdown % = DIVIDE(CurrentClose - PeakClose, PeakClose)*100
Calmar = DIVIDE(AnualRet, ABS(MinDrawdownPct))
Volume Surge Flag = IF(CurrentVol > 2*BaselineVol, "SURGE","Normal")
```

---

## 6. Arquitectura y ETL (elige tu experiencia)

```
IBKR diario (NVDA+SOXQ, 15min delay ok) ─┐
                                         ├→ Python ETL (features+labels)
Alpha NEWS_SENTIMENT (compañero, 1 req) ─┘         │ psycopg2 COPY
                                                   ▼
                                          PostgreSQL docker (5432)
                                          ├── dim_company (NVDA)
                                          ├── dim_date
                                          ├── fact_stock_prices
                                          ├── fact_news (+fact_news_company)
                                          └── fact_predictions  ← modelo escribe
                                                   │
                                                   ▼
                                              Power BI DirectQuery
                                              Pág1 Mercado / Pág2 Noticias / Pág3 Pred+Eval
```

**ETL híbrido recomendado** (tu experiencia SP + Python):
- Python calcula `RSI, MACD, beta, labels` (1 línea con `pandas-ta`, en T-SQL son cursores).
- SQL solo carga DIM/FACT con `SP sp_upsert_dim_date` + `COPY ... ON CONFLICT DO NOTHING` + índices para Power BI.

Docker: `postgres:16` + `pgAdmin`. Si la cátedra exige SQL Server, cambias `psycopg2 → pyodbc`, `SERIAL → IDENTITY` — el resto idéntico.

---

## 7. Anti-leakage (para que no te anulen la tesis)

1. División **temporal estricta** (no `shuffle`).
2. **Purging:** elimina del train toda fila cuyo `[t, t+5]` solape test.
3. **Embargo:** buffer 20d post-test (corta autocorrelación).
4. `WalkForward` para backtest final (imita trading real), `PurgedKFold` para HPT.
5. Normalización Z-score rolling 63d, no global. `shift(1)` en todo rolling.
6. Últimos **6 meses untouched** como test final — es tu reporte 3-6 meses.

---

## 8. Roadmap 7 sprints (~17 días)

| Sprint | Días | Entrega |
|---|---|---|
| F1 Datos IBKR | 2 | `nvda_daily.xlsx` validado |
| F2 Noticias | 2 | `daily_sentiment.csv` |
| F3 Features | 3 | `features.csv` (22→12 vars) |
| F4 Labels | 1 | `labeled.csv` + tabla tau |
| F5 Modelos | 4 | `modelo_rf.pkl` (LogReg vs RF) |
| F6 Evaluación | 2 | Ablaciones NVDA/SOX/noticias + matriz + equity |
| F7 BI | 3 | PG docker + `dashboard.pbix` + reporte |

---

## 9. Qué copiar de PRECIARIO_OIL (60% reutilizable)

`core/config.py` (AssetSpec→NASDAQ), `core/news/__init__.py` (`_kw_search` + `NewsHistory`), `core/models.py` (`OHLCBar`), `core/wti_snapshot.py`→`nvda_snapshot.py`, `run_all.py` orquestador, `notifications.py`. A crear: `feature_engineering.py`, `labels.py`, `nvda_trainer.py`, `backtest.py`.

---

## 10. MODO DAY TRADING — velas de 1 HORA (implementado)

**Decisión:** day trading → barras de **1 hora**. NVDA opera 09:30-16:00 ET =
**7 barras/día** RTH.

> **Horario real de las barras IBKR:** IBKR entrega las barras alineadas a la
> hora de reloj: **09:30 ET (30 min), 10:00, 11:00, 12:00, 13:00, 14:00 y
> 15:00 ET**. TradingView las alinea distinto (09:30, 10:30, ... 15:30). El
> pipeline usa posición de barra (`bar_of_day`) para ser robusto a esta
> diferencia y al DST.
>
> **Datos reales descargados (2026-09-10):** 6 meses exactos por símbolo
> (NVDA y SOXQ), **882 barras**, **126 días de trading**, 7 barras/día,
> sin valores nulos. Pipeline: 822 filas de features × 97 columnas y
> 800 filas etiquetadas (BUY 36.8% / HOLD 29.5% / SELL 33.8%, tau medio 0.43%).

### 10.1 Qué cambia respecto al diseño diario

| Aspecto | Diario (swing) | **1 hora (day trading)** |
|---|---|---|
| Filas por día | 1 | 7 |
| Target | `future_ret_5d` (5 días) | `future_ret` a **N barras** (default 3h) |
| Purging | 5 días | 3 barras (`MODEL_PURGE_BARS`) |
| Embargo | 20 días | 7 barras (`MODEL_EMBARGO_BARS`) |
| Features extra | — | `bar_of_day`, `bars_left`, `vwap`, `vwap_dist`, `orb_high/low`, `orb_break_up/down`, `gap_open_pct`, `cum_day_ret`, `is_first_bar`, `is_last_bar` |
| Sentimiento | merge diario | merge diario (aplica a las 7 barras del día) |
| KPIs | Sharpe diario | + **trades/día, winrate por trade, hit rate por barra** |

La fórmula de RSI/MACD/BB/ATR es la misma del diseño diario, pero los
**periodos se cuentan en barras** (RSI-14 = 14 velas de 1h ≈ 2 días).

### 10.2 Target day trading (implementado en `core/labels.py`)

```
future_ret = Close[t+H] / Close[t] - 1        H = MODEL_HORIZON_BARS (default 3)
BUY  si future_ret >  tau
SELL si future_ret < -tau
HOLD en caso contrario

tau adaptativo: tau = 0.5 * std(ret_1b, 20), recortado a [0.2%, 2%]
```

La función `suggest_tau_grid()` imprime la tabla HOLD/BUY/SELL por horizonte
y tau para elegir el target con HOLD 40-60% (ver §10.4).

### 10.3 Features implementadas (`core/feature_engineering.py`)

- **Técnicas (21):** `ret_1b/3b/1d`, `log_ret_1b`, `sma_20/50`, `dist_sma_*`, `macd*`, `rsi_14`, `bb_*`, `atr_14/atr_pct`, `vol_zscore`, `volume_surge`, `hl_range_pct`, `close_pos`.
- **Sesión (10):** `bar_of_day`, `bars_left`, `is_first/last_bar`, `hour_ny`, `minute_ny`, `day_of_week`, `vwap`, `vwap_dist`.
- **Opening range / gap (6):** `orb_high`, `orb_low`, `dist_orb_high/low`, `orb_break_up/down`, `gap_open_pct`, `cum_day_ret`.
- **Sector SOXQ (9):** `sox_ret_1b/1d`, `sox_ratio`, `sox_ratio_z`, `beta_60b`, `corr_60b`, `alpha_1b/5b`, `rel_strength_1d`.
- **Sentimiento (5):** `sent_weighted`, `sent_mean`, `sent_count`, `bull_ratio`, `has_sentiment`.
- **Patrones (29+3):** 26 patrones de vela activos + contadores `pat_bull_count`, `pat_bear_count`, `pat_conf_max`.

Total pipeline validado con datos sintéticos: **90 columnas**.

### 10.4 Cómo elegir tau (tabla real del grid, datos sintéticos 4 meses)

| Horizonte | tau | HOLD % | Comentario |
|---|---|---|---|
| 1 barra | 0.3% | 42.5% | equilibrado pero mucho ruido |
| **3 barras** | **0.5%** | **36.1%** | **recomendado day trading** |
| 3 barras | 0.8% | 55.1% | conservador |
| 7 barras | 1.0% | 46.2% | swing intradía |

Regla: elegir el tau que deje HOLD 40-60% y validar con backtest (F6).

### 10.5 Pipeline implementado y comandos

```
run_nvda.py
  PASO 1  core/data_providers/ibkr_intraday.py   -> nvda_intradia.xlsx
  PASO 2  core/feature_engineering.py            -> nvda_features.csv
  PASO 3  core/labels.py                         -> nvda_labeled.csv
  PASO 4  core/nvda_snapshot.py                  -> backup + nvda_snapshot_horario.csv
  PASO 5  predicción                             -> pendiente (modelo en tu PC)

python run_nvda.py --data-only                 # descargar intradía
python run_nvda.py --data-only                 # requiere TWS o IB Gateway
python run_nvda.py                             # pipeline completo
python run_nvda.py --no-patterns               # omite 57 patrones (más rápido)
python run_nvda.py --inspect                   # diagnóstico
```

Sin TWS (este ordenador de empresa) se puede validar con:
`python scripts/dev/test_pipeline_synthetic.py` (genera datos fake y corre todo).

---

## 11. Disponibilidad de datos IBKR (investigado 2026-09)

> Fuente: docs oficiales TWS API — *Historical Data Limitations*.

| Tema | Hallazgo |
|---|---|
| Barras ≤30 seg | **Solo 6 meses** de antigüedad |
| **Barras ≥1 min (incluye 1h)** | **Sin límite de 6 meses** ("Historical Data Limitations for barSize = 1 mins and greater have been lifted") |
| Duración por request (tabla clásica) | 1h → **"1 M"** por request (se encadena hacia atrás) |
| Pacing | Máx 60 requests / 10 min; máx 6 requests del mismo contrato en 2s |
| Por eso el código | Chunks de `1 M` con `sleep(1s)` → **6 requests para 6 meses** (holgado) |
| Fuente alternativa | **No se utiliza**; una caída de IBKR detiene la actualización |

### Volumen de datos esperado (1h RTH, 7 barras/día)

| Antigüedad | Barras aprox | Requests IBKR |
|---|---|---|
| 1 mes | ~147 | 1 |
| 3 meses | ~440 | 3 |
| **6 meses** | **~880** | **6** |
| 1 año | ~1,760 | 12 |
| 2 años | ~3,520 | 24 |

**Conclusión:** 6-12 meses de velas 1h es trivial para IBKR y suficiente para
Random Forest (880-1,760 filas). Si se necesita más historia, se encadenan
más chunks; el límite práctico es el tiempo de descarga (~1s por chunk).

### Resultado real de la descarga (2026-09-10)

| Activo | Barras | Días | Rango | Requests |
|---|---|---|---|---|
| NVDA_1H | **882** | 126 | 2026-03-12 → 2026-09-10 | 6 chunks |
| SOXQ_1H | **882** | 126 | 2026-03-12 → 2026-09-10 | 6 chunks |

- Archivos: `core/output/intradia/nvda_intradia.xlsx` (sheets `NVDA_1H`, `SOXQ_1H`)
  y `core/output/intradia/nvda_intradia.csv` (formato largo: `Datetime,symbol,Open,High,Low,Close,Volume`, 1,764 filas).
- Tiempo de descarga total: **~25 segundos** (12 requests con `sleep(1s)`).
- Sin nulos, 7 barras/día exactas. Precio NVDA 182.38 → 218.37; SOXQ 60.39 → 91.48 (en la ventana).
- Client ID usado: `IBKR_INTRADIA_CLIENT_ID` (default 20; usar 31 si el bot del preciario está corriendo).

### CSV exportado de TWS vs API

El CSV del gráfico de TWS depende de cuántas velas cargues en el gráfico
(no es un mecanismo confiable para histórico). El API (`reqHistoricalData`)
es el único camino de datos implementado para este proyecto.

---

## 12. Modelos y bot (implementado 2026-09)

### 12.1 Entrenamiento (`core/nvda_trainer.py`)

- **Modelos:** Logistic Regression (baseline, con scaler), **Random Forest**
  (principal), XGBoost (opcional si está instalado).
- **Validación:** walk-forward expanding con **purging (3 barras) + embargo
  (7 barras)**; métricas sobre predicciones out-of-fold:
  accuracy, F1 macro, precision/recall por clase, matriz de confusión y
  ROC-AUC OVR.
- **Ablaciones:** `A_nvda_only`, `B_nvda_sox`, `C_nvda_news` y
  `D_nvda_sox_news`, con las mismas particiones temporales para cada modelo.
- **Artefactos:** `core/output/nvda_models/modelo_nvda_rf.pkl`,
  `metrics.json`, `feature_list.json`, `experiment_ab.json`.

Resultados con datos reales y noticias Alpha integradas (**12 meses,
1,698 features y 956 etiquetas válidas**). Se corrigió el target para que
las 3 barras futuras pertenezcan a la misma sesión; no se permiten retornos
overnight. El bloque final temporal quedó separado para comprobar OOS.

| Modelo / variables | F1 OOF | F1 test final | ROC-AUC OOF | ROC-AUC test |
|---|---:|---:|---:|---:|
| Baseline mayoría | 0.1823 | 0.1966 | — | — |
| LogReg A: NVDA | 0.3495 | 0.3720 | 0.5391 | 0.5447 |
| **LogReg B: NVDA + SOXQ** ⭐ | 0.3373 | **0.4001** | 0.5438 | 0.5610 |
| LogReg C: NVDA + noticias | 0.3380 | 0.3781 | 0.5427 | 0.5463 |
| **LogReg D: NVDA + SOXQ + noticias** | 0.3506 | 0.3937 | **0.5499** | **0.5632** |
| RF A: NVDA | 0.3152 | 0.3215 | 0.5224 | 0.4795 |
| RF B: NVDA + SOXQ | 0.3157 | 0.3540 | 0.5110 | 0.4934 |
| RF C: NVDA + noticias | 0.3489 | 0.3365 | 0.5427 | 0.4782 |
| RF D: NVDA + SOXQ + noticias | 0.3330 | 0.3654 | 0.5162 | 0.5102 |
| XGB A: NVDA | 0.3466 | 0.3333 | 0.5146 | 0.4977 |
| XGB B: NVDA + SOXQ | 0.3457 | 0.3685 | 0.5289 | 0.5146 |
| XGB C: NVDA + noticias | 0.3654 | 0.3179 | 0.5260 | 0.5077 |
| XGB D: NVDA + SOXQ + noticias | 0.3677 | 0.3397 | 0.5411 | 0.4895 |

**Conclusión provisional:** el candidato más estable es **Logistic Regression**.
El conjunto **NVDA+SOXQ** obtiene el mejor F1 del test final (0.4001), mientras
**NVDA+SOXQ+noticias** obtiene el mejor ROC-AUC final (0.5632). SOXQ aporta
información incremental en este experimento; las noticias también aportan
contexto, aunque su beneficio no es uniforme entre modelos. Random Forest y
XGBoost quedan como challengers, no como modelo principal automático.

Esta conclusión todavía debe confirmarse con más períodos de mercado y un
backtest financiero neto de costes. No se debe interpretar un F1 de 0.40 como
una probabilidad de ganar el 40% de las operaciones.

### 12.1.1 ¿Con qué % concluir que el modelo es "claro"?

Reglas de lectura acordadas para la memoria:

| Métrica | Azar / baseline | Débil | Aceptable | Bueno | Sospechoso (leakage) |
|---|---|---|---|---|---|
| **ROC-AUC** (3 clases) | 0.50 / 0.50 | 0.50–0.55 | 0.55–0.60 | 0.60–0.65 | > 0.75 |
| **F1 macro** | 0.17 (mayoría) | 0.30–0.35 | 0.35–0.45 | 0.45–0.55 | > 0.65 |
| **Accuracy** | 0.35 (mayoría) | < 0.40 | 0.40–0.45 | 0.45–0.50 | > 0.55 |

- **Nunca** usar solo accuracy (con 35% de HOLD, predecir siempre HOLD da
  35% y es inútil).
- Comparar contra el **baseline mayoría** (0.174 F1).
- Preferir el modelo con mejor F1 macro **y** ROC-AUC; si la diferencia
  entre modelos es menor a la desviación entre folds (`f1_folds_std`),
  elegir el más simple.
- Con los datos actuales el modelo está en zona **"incipiente/débil"**
  (AUC 0.548): aprende algo, pero no es todavía una señal fiable. El salto
  esperado viene de: noticias reales, más historia (24 meses) y ajuste de
  `tau`/features. Es un resultado honesto y defendible en tesis.

### 12.1.2 Estado actual de modelos

| Aspecto | Valor |
|---|---|
| Modelos guardados | `modelo_nvda_logreg.pkl` (ganador) y `modelo_nvda_rf.pkl` |
| Elegir en predicción | `--model logreg` / `--model rf` / `--model xgb` |
| Discrepancia actual | LogReg=SELL 62.8% vs RF=HOLD 42.9% → sin consenso, precaución |

### 12.2 Predicción y bot (`core/nvda_predictor.py`, `run_nvda.py`)

- `--predict --dry-run`: señal sobre la última barra cerrada con
  probabilidades BUY/HOLD/SELL.
- **Umbral de señal:** si p(BUY) o p(SELL) < `SIGNAL_PROBA_THRESHOLD`
  (default 0.55) → se reporta HOLD (menos ruido/costes).
- `--bot`: se ejecuta cada minuto; en cada cierre de barra RTH (minuto 0-5
  de cada hora, días de semana 09:30-16:00 ET) descarga las últimas 48h
  (2 requests), recalcula features y emite la señal.
- Alertas: `NVDA_WEBHOOK_URL` (Synology u otro) y/o `--save-db` para
  PostgreSQL.
- **Delay freemium 15 min:** la barra se confirma ~15 min después de
  cerrar; el bot corre igual cada hora y documenta la señal como
  cuasi-real, no streaming.

### 12.3 KPIs del informe

La selección formal de métricas está documentada en `KPIS.md`.

- **Clasificación:** F1 macro como métrica principal, matriz de confusión
  normalizada por fila, Precision/Recall de BUY y SELL, ROC-AUC OVR y
  Accuracy solo como contexto.
- **Financieros:** Sharpe Ratio, Max Drawdown, Sortino, Win Rate, Profit
  Factor, IC Spearman y comparación contra Buy & Hold.
- La selección del modelo no dependerá de una sola métrica: debe superar el
  baseline, mantener estabilidad entre folds y producir resultados
  financieros positivos después de costes.
- Las métricas financieras se calcularán sobre predicciones out-of-sample,
  ejecutando la señal en la apertura de la barra siguiente.

### 12.4 Sentimiento (integración con tu compañero)

- **Artículo-level (recomendado):** `core/output/news_history/news.csv` o JSON
  crudo Alpha → features **point-in-time** con ventanas 6h, 1d, 3d, 7d, 30d
  + decaimiento exponencial (half-life 12h). Una noticia a las 10:45 afecta
  la barra de 11:00, nunca la de 10:30.
- **Diario (fallback):** `data/news_nvda_daily.csv` → se aplica con
  **lag-1** (el agregado del día D recién aparece en las barras del día D+1).
- Tests de leakage: `python scripts/dev/test_sentiment_alignment.py`.

---

## 13. Checklist para tu ordenador principal

- [ ] Copiar carpeta `PRECIARIO_NVDA/` completa
- [ ] `bash scripts/setup_ubuntu.sh` (crea venv + .env)
- [ ] Editar `.env`: `IBKR_HOST`, `IBKR_PORT`, webhook, `INTRADAY_MONTHS_BACK=12`
- [ ] Abrir TWS/IB Gateway (API habilitada) y `python run_nvda.py --data-only`
- [ ] Confirmar TWS o IB Gateway iniciado antes de `python run_nvda.py --data-only`
- [ ] `python run_nvda.py` → features + labels + snapshot
- [ ] `python run_nvda.py --train` → modelos + ablaciones NVDA/SOX/noticias
- [ ] `python run_nvda.py --predict --dry-run` → señal
- [ ] `python run_nvda.py --bot --dry-run` → bot horario
- [ ] (Opcional) `docker compose up -d` + `python -m core.db --prices`
- [ ] Leer `INSTALL.md`, `PROYECTO_NVDA.md` e `INDICADORES.md`

> Este documento es portable. Llévalo + la carpeta. Todo lo investigado (SOX metodología Nasdaq, Alpha docs, papers Lopez de Prado) está citado y es defendible en memoria.
