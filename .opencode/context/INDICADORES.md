# Indicadores y Variables — PRECIARIO_NVDA

> **Para tu PowerPoint.** Cada ficha = 1-2 diapositivas. Copia tal cual al PPTX.  
> Basado en `PRECIARIO_OIL/core/pattern_detector.py` (57 patrones) + `core/models.py:OHLCBar` + investigación web para NVDA/SOX.

---

## Cómo usar este documento

- **Sección 1:** OHLC base (lo que viene de IBKR).
- **Sección 2:** Features técnicos (los que calculas tú).
- **Sección 3:** Features de sector (SOXQ benchmark).
- **Sección 4:** Features de sentimiento (Alpha).
- **Sección 5:** Patrones de velas japonesas (heredados del preciario).
- **Sección 6:** Target y matriz final para el modelo.

Cada variable tiene: **Qué es · Fórmula · Cómo se lee · Por qué sirve para BUY/HOLD/SELL · Ejemplo NVDA**.

> **NOTA DAY TRADING (2026-09):** el proyecto pasó a modo intradía con velas de
> **1 hora**. Las secciones 2-6 siguen siendo válidas (mismas fórmulas, periodos
> contados en barras); las **secciones 8-10** agregan las variables específicas
> de sesión intradía y el target day trading.

---

## 1. Variables base — OHLCV (vienen de IBKR)

### 1.1 Open / High / Low / Close

| Campo | Descripción |
|---|---|
| `Open` | Precio primera transacción del día |
| `High` | Máximo del día |
| `Low` | Mínimo del día |
| `Close` | Último precio (el que usa el modelo como referencia) |

No se usan sueltos; son la materia prima de todo lo demás. En `PRECIARIO_OIL` se guardan como `TradeDate, Open, High, Low, Close, Volume` en `nvda_daily.xlsx` sheets `NVDA_DAILY/SOXQ_DAILY`.

### 1.2 Volume

Cantidad de acciones negociadas. **Acompaña siempre al precio.** Subida con volumen alto = convicción; subida con volumen bajo = trampa.

### 1.3 OHLCBar (reutilizado de `PRECIARIO_OIL/core/models.py:97`)

```python
bar.is_bullish      # Close > Open
bar.body_size       # |Close-Open|
bar.upper_shadow    # High - max(Open,Close)
bar.lower_shadow    # min(Open,Close) - Low
bar.range_size      # High - Low
```
Estas 5 propiedades son las que usa el detector de patrones para decidir si una vela es martillo, doji, envolvente, etc.

---

## 2. Features técnicos — Mercado NVDA solo

### 2.1 `ret_1d` — Retorno diario

- **Fórmula:** `(Close_t - Close_{t-1}) / Close_{t-1}`
- **Lectura:** `+2.1%` = subió 2.1% hoy. `-1.8%` = cayó.
- **Por qué:** es la variable más cercana al target. El modelo aprende "¿qué patrones de ret preceden a un BUY?".
- **Ejemplo NVDA:** 2025-01-27 (DeepSeek) `ret_1d = -17%` → extremo que el modelo debe aprender a no extrapolar lineal.

### 2.2 `log_ret`

- **Fórmula:** `ln(Close_t / Close_{t-1})`
- **Lectura:** casi idéntico a `ret_1d` para movimientos pequeños, pero **estacionario** (requisito estadístico). Si usas `Close` crudo, el modelo cree que `$800 es distinto de $400` por escala.
- **Por qué:** evita que el modelo memorice niveles de precio.

### 2.3 `ret_5d` y `ret_10d`

- **Fórmula:** `(Close_t - Close_{t-5})/Close_{t-5}`
- **Lectura:** momentum semanal / quincenal.
- **Por qué:** horizonte del target es 5 días — el modelo necesita contexto de la misma ventana.

### 2.4 `SMA_10 / SMA_20 / SMA_50`

- **Fórmula:** `SMA_n = mean(Close últimos n días)`
- **Lectura:** `Close > SMA_20` = tendencia alcista de corto plazo. `SMA_10 cruza SMA_20` = cambio de régimen.
- **Por qué:** `SMA_50` es soporte institucional NVDA. Feature derivado útil: `dist_SMA20 = (Close - SMA20)/SMA20`.

### 2.5 `EMA_12 / EMA_26`

- **Fórmula:** `EMA_t = alpha*Close_t + (1-alpha)*EMA_{t-1}`, `alpha=2/(n+1)`
- **Lectura:** igual que SMA pero pesa más lo reciente. Reacciona antes.
- **Por qué:** base del MACD.

### 2.6 `RSI_14` ⭐

- **Fórmula:** `RS = avg_gain_14 / avg_loss_14`, `RSI = 100 - 100/(1+RS)`
- **Lectura:** `0-100`. `>70` sobrecompra, `<30` sobreventa. `RSI 97` (NVDA 2024-01-24) = euforia extrema, probable reversa.
- **Por qué:** top 3 en `feature_importances_` de Random Forest en papeles NVDA-like. Señal directa para SELL (sobrecompra) y BUY (sobreventa).

### 2.7 `MACD (12,26,9)` — `MACD_line, Signal, Histogram`

- **Fórmula:** `MACD = EMA12 - EMA26`, `Signal = EMA9(MACD)`, `Hist = MACD - Signal`
- **Lectura:** `MACD cruza Signal hacia arriba` = BUY. `Hist negativo y cayendo` = momentum bajista acelerando (NVDA 2025-04-08: `-10.71` vs `-7.70`).
- **Por qué:** confirma tendencia sin mirar precio absoluto.

### 2.8 `BB_width / %B` (Bollinger)

- **Fórmula:** `Mid=SMA20`, `Upper=Mid+2*STD20`, `Lower=Mid-2*STD20`, `%B=(Close-Lower)/(Upper-Lower)`, `Width=(Upper-Lower)/Mid*100`
- **Lectura:** `%B>1` toca techo, `%B<0` toca suelo. `Width` grande = alta volatilidad.
- **Por qué:** NVDA 2026-04-17 `Close 201.68 Upper 202.91` (%B ≈0.95) = cerca de techo, HOLD/SELL más probable.

### 2.9 `ATR_14`

- **Fórmula:** `TR = max(High-Low, |High-PrevClose|, |Low-PrevClose|)`, `ATR=SMA(TR,14)`
- **Lectura:** volatilidad en dólares. `ATR 8.2` con `Close 400` = 2% diario.
- **Por qué:** sirve para `tau_adaptativo` y para normalizar: `tau_adapt = 0.5 * ATR/Close`.

### 2.10 `vol_zscore / volume_surge`

- **Fórmula:** `z = (Volume - SMA20_vol)/STD20_vol`, `surge = 1 si Volume >2*SMA20_vol`
- **Lectura:** `z=3.2` = volumen 3 sigmas sobre lo normal → evento.
- **Por qué:** subida con `surge=1` tiene convicción. Subida sin volumen = falsa.

---

## 3. Features de sector — NVDA vs SOXQ

> SOXQ = ETF Invesco PHLX Semiconductor (réplica exacta del índice SOX de 30 semis). Sin SOX no sabrías si NVDA sube porque *todo el sector* sube.

| Feature | Fórmula | Lectura PPT |
|---|---|---|
| `sox_ratio` | `Close_NVDA / Close_SOXQ` | >sube = NVDA lidera sector |
| `sox_ratio_z60` | `(ratio - SMA60)/STD60` | `z>2` = NVDA carísimo vs sector → mean-reversion SELL |
| `beta_60` | `Cov(R_NVDA,R_SOXQ)/Var(R_SOXQ)` 60d | ~2.3 largo plazo, ~0 en 2026 desacople. Model needs to know. |
| `corr_60` | `Corr(R_NVDA,R_SOXQ,60)` | `<0.3` = régimen desacoplado, desactiva hedge |
| `alpha_5d` | `mean(R_NVDA - beta*R_SOXQ, 5)` | Residual idiosincrático: `alpha>0` = NVDA hace algo propio (BUY) |

---

## 4. Features de sentimiento — Alpha Vantage

> Vienen del JSON que te pasa tu compañero (`feed[].ticker_sentiment[]`).

| Feature | Cómo se calcula | Lectura |
|---|---|---|
| `sent_weighted` | `sum(score*relevance)/sum(relevance)` donde `ticker=="NVDA" && relevance>0.3` | `-1 a +1`. `+0.42 Bullish` = noticias a favor |
| `sent_mean` | `mean(score)` | Promedio simple |
| `sent_count` | `n` noticias del día | `count=1` con `score=+0.9` es menos fiable que `count=12` con `+0.2` |
| `sent_7d` | `rolling_mean(sent_weighted,7)` | Tendencia de ánimo semanal |
| `bull_ratio` | `N_pos/N_total` | `0.8` = 80% positivas |

**Threshold Alpha oficial:** `<= -0.35 Bearish`, `-0.35 a -0.15 Somewhat-Bearish`, `-0.15 a 0.15 Neutral`, `0.15 a 0.35 Somewhat-Bullish`, `>=0.35 Bullish`. Para el modelo usas el score continuo, no el label.

FinBERT (opcional): reemplazas `score` por `FinBERT(title+summary)` si quieres comparar `Modelo B (Alpha) vs B2 (FinBERT)`.

---

## 5. Patrones de velas japonesas — los 57 del preciario

> **Sí existe el agente.** `PRECIARIO_OIL/core/pattern_detector.py` (770 líneas) y `core/models.py:PatternType` ya implementan 57 patrones. Los reutilizas 1:1 — solo cambia `preciario → señal`.

### 5.1 Cómo los usa tu modelo

No intentes que el modelo "aprenda velas" desde OHLC crudo. El detector ya las codifica como **features discretas**:

```
Cada día t: detector.detect_all(df últimos 50 días)
→ lista de PatternResult(pattern=HAMMER, signal=BULLISH, confidence=0.65)
→ features: hammer=1, engulfing_bull=0, doji=1, volume_climax=0 ...
→ el Random Forest las usa como cualquier otra columna
```

Ventaja: 57 patrones → 57 columnas binarias + `confidence`. El modelo aprende "Hammer + RSI<35 → BUY".

### 5.2 Candlestick (27 patrones, ventana 1-3 velas) — los que van al PPT

| Patrón | Señal | Qué ve el ojo | Para BUY/HOLD/SELL |
|---|---|---|---|
| **Doji** / **Dragonfly Doji** / **Gravestone Doji** / **Long-legged Doji** | NEUTRAL / BULLISH / BEARISH | Cuerpo diminuto, sombras largas. Dragonfly = sombra inferior larga (rechazo de mínimos). Gravestone = sombra superior (rechazo de máximos). | Dragonfly con `RSI<30` → BUY. Gravestone con `RSI>70` → SELL. Doji solo = HOLD (indecisión). |
| **Hammer** | BULLISH (0.65) | Sombra inferior >60% del rango, cuerpo <30%, en tendencia bajista previa | Soporte encontrado → BUY |
| **Hanging Man** | BEARISH (0.55) | Igual forma que Hammer pero tras subida | Techo → SELL |
| **Inverted Hammer** | BULLISH (0.60) | Sombra superior larga tras caída | Posible reversa → BUY |
| **Shooting Star** | BEARISH (0.65) | Sombra superior larga tras subida | Techo → SELL |
| **Bullish Engulfing** | BULLISH (0.70) | Vela alcista envuelve cuerpo previo bajista | Reversa fuerte → BUY |
| **Bearish Engulfing** | BEARISH (0.70) | Vela bajista envuelve alcista previa | Reversa fuerte → SELL |
| **Bullish Harami** | BULLISH (0.60) | Vela pequeña alcista dentro del cuerpo bajista previo | Reversa débil → HOLD/BUY si confirma |
| **Bearish Harami** | BEARISH (0.60) | Pequeña bajista dentro de alcista | HOLD/SELL |
| **Piercing Line** | BULLISH (0.65) | Abre bajo Low previo, cierra >50% del cuerpo previo | BUY |
| **Dark Cloud Cover** | BEARISH (0.65) | Abre sobre High previo, cierra <50% del cuerpo previo | SELL |
| **Tweezer Top / Bottom** | BEARISH / BULLISH (0.55) | Dos High iguales / dos Low iguales | Doble techo/suelo → SELL/BUY |
| **Belt Hold Bullish / Bearish** | BULLISH / BEARISH (0.60) | Cuerpo >70% del rango, abre en Low/High | Fortaleza direccional |
| **Three White Soldiers** | BULLISH (0.75) | 3 velas alcistas crecientes consecutivas | Momentum fuerte → BUY |
| **Three Black Crows** | BEARISH (0.75) | 3 bajistas decrecientes | SELL |
| **Morning Star** | BULLISH (0.70) | Bajista + vela pequeña + alcista que recupera >50% | Reversa mañana → BUY |
| **Evening Star** | BEARISH (0.70) | Alcista + pequeña + bajista | Reversa tarde → SELL |
| **Three Inside Up / Down** | BULLISH / BEARISH (0.70) | Harami + confirmación tercera vela | BUY/SELL confirmado |

### 5.3 Chart patterns (30 patrones, ventana 20/30/50 días)

| Patrón | Señal | Idea en 1 frase para PPT |
|---|---|---|
| **Double Top / Bottom** | BEARISH / BULLISH (0.70) | Dos techos/suelos iguales (±2%) + ruptura de neckline. Clásico. |
| **Head and Shoulders / Inverse** | BEARISH / BULLISH (0.75) | 3 picos donde cabeza > hombros. Confirmado si Close rompe neckline. |
| **Ascending / Descending / Symmetrical Triangle** | BULLISH / BEARISH / NEUTRAL (0.65) | High plano + Low subiendo = acumulación alcista, etc. Pendientes por regresión. |
| **Bullish / Bearish Flag** | BULLISH / BEARISH (0.65) | Tendencia fuerte >5% + consolidación leve opuesta = continuación. |
| **Cup and Handle / Pennant / Rectangle / Wedge / Diamond ...** | Según tipo | Continuación o reversa de mediano plazo. Menos frecuentes en NVDA diario. |

### 5.4 Volume patterns

| Patrón | Señal | Lectura |
|---|---|---|
| **Volume Climax** | BULLISH/BEARISH (0.60) | Volumen >2× promedio → clímax de participación. Si Close sube = compra fuerte. |
| **Low Volume Test** | NEUTRAL (0.50) | Volumen <0.5× promedio → nadie opera, HOLD. |

**Tip para PPT:** no expongas los 57. Usa una slide con 4 columnas (Reversa / Continuación / Candlestick / Volumen) y destaca 8-10: `Hammer, Engulfing, Morning/Evening Star, Three Soldiers/Crows, Double Top/Bottom, H&S, Flag, Volume Climax`. El resto queda en anexo.

---

## 6. Target y dataset final

```
future_ret_5d = (Close_{t+5} - Close_t)/Close_t
if future_ret > +tau  → BUY   (ej tau=1.2% adaptativo)
elif future_ret < -tau → SELL
else HOLD
```
`tau` elegido para dejar `HOLD 40-60%` (con `tau=1% N=1` sale 86% HOLD — inútil). Guarda `label_start/end` para purging.

**Fila final por día:**
```
Date | NVDA Close | ret_1d | SMA20 | RSI14 | MACD_hist | BB_%B | vol_zscore
     | sox_ratio_z | beta_60 | alpha_5d | sent_weighted | sent_count
     | hammer | engulfing_bull | doji | double_top | ... (57 binarias)
     | label (BUY/HOLD/SELL) | future_ret_5d
```

---

## 7. Glosario rápido para el jurado

- **Alpha/Beta:** beta = cuánto se mueve NVDA cuando SOX se mueve 1%. Alpha = lo que hace NVDA por sí mismo.
- **Purging/Embargo:** quitar del train las filas cuyo futuro solapa el test, para no hacer trampa temporal.
- **F1 macro:** promedio de F1 de BUY, HOLD, SELL sin ponderar — castiga si ignoras una clase.

---

## 8. Variables INTRADÍA (DAY TRADING, velas 1h)

> NVDA cotiza 09:30-16:00 ET = **7 barras de 1h por día**. Estas variables
> solo existen en el modo intradía y son las que diferencian el proyecto
> day trading del diseño swing diario.

### 8.1 Contexto de sesión

| Variable | Fórmula | Lectura para day trading |
|---|---|---|
| `bar_of_day` | posición de la barra en el día (1..7) | barra 1 = apertura; barra 7 = cierre |
| `bars_left` | 7 − `bar_of_day` | cuántas barras quedan para cerrar posición |
| `is_first_bar` | 1 si `bar_of_day==1` | señal de apertura (más volumen) |
| `is_last_bar` | 1 si `bar_of_day==7` | cierre de sesión (riesgo overnight) |
| `hour_ny`, `minute_ny` | hora ET de la barra | 9:30, 10:30 ... 15:30 |
| `day_of_week` | 0=lunes ... 4=viernes | lunes/ viernes suelen tener otro comportamiento |
| `gap_open_pct` | `(Open_bar1 / Close_ayer_bar7) − 1` | gap alcista/bajista al abrir |
| `cum_day_ret` | `Close / Open_bar1 − 1` | retorno acumulado del día |

### 8.2 VWAP y Opening Range (las 2 más usadas en day trading)

| Variable | Fórmula | Lectura |
|---|---|---|
| `vwap` | `Σ(precio típico × volumen) / Σvolumen` acumulado del día | precio promedio ponderado institucional |
| `vwap_dist` | `(Close − VWAP) / VWAP` | `>0` precio sobre VWAP = compradores controlan; `<0` vendedores |
| `orb_high` / `orb_low` | máximo/mínimo de la **primera hora** | rango de apertura |
| `dist_orb_high` | `(Close − orb_high)/orb_high` | ruptura alcista del rango |
| `orb_break_up` / `orb_break_down` | 1 si `Close > orb_high` / `< orb_low` | señal clásica ORB de momentum |

### 8.3 Barras vs días

Los indicadores de la sección 2 (RSI, MACD, BB, ATR, SMA) se calculan con
**periodos en barras**, no en días:

- `rsi_14` = RSI de 14 velas de 1h (≈ 2 días de trading).
- `sma_20` = media de 20 horas (≈ 3 días).
- `atr_14` / `atr_pct` = volatilidad por hora; se usa para el `tau` adaptativo.
- `beta_60b`, `corr_60b`, `sox_ratio_z` = ventana de 60 barras (≈ 8.5 días).

---

## 9. Target INTRADÍA

```
precio_base = adj_close si existe (IBKR ADJUSTED_LAST) | Close crudo (IBKR TRADES)

future_ret = precio_base[t+H] / precio_base[t] - 1     (H = 3 barras, retorno SIMPLE)
BUY  si future_ret >  tau
SELL si future_ret < -tau
HOLD en caso contrario

tau = 0.5 × std(ret_1b, 20)  recortado entre 0.2% y 2%
```

Se guarda `label_start`, `label_end` y `label_horizon_bars` para el purging
(siempre en barras). La tabla `suggest_tau_grid()` imprime HOLD/BUY/SELL por
horizonte y tau; se elige el que deje HOLD 40-60%. Las barras futuras se
buscan dentro de la misma `date_ny`; por tanto, el target no mezcla el cierre
de una sesión con la apertura del día siguiente.

### 9.1 ¿Adj Close o Close? ¿Simple o logarítmico?

| Pregunta | Decisión del proyecto | Razón |
|---|---|---|
| **¿Qué precio?** | `Adj Close` si está disponible; si no, `Close` crudo de IBKR (`TRADES`) | Los ajustes importan en horizontes multi-día (splits/dividendos). NVDA no tuvo split en la ventana y el dividendo es ~0.01-0.03% → efecto despreciable intra-día, pero se soporta formalmente |
| **¿Simple o log?** | Target **simple** (`P1/P0−1`); feature de retornos también en **log** (`log_ret_1b`) | El simple es lo que realmente se gana/pierde y hace legible el umbral ±tau%. Para |r|<2% ambos difieren <0.02%. El log es estacionario/aditivo, por eso se usa como feature |
| **¿Cómo cambiar?** | `MODEL_TARGET_MODE=simple` (default) o `=log` en `.env` | Si se cambia a log, el tau queda en escala log y los labels se recalculan |
| **¿Cómo cambiar el precio?** | `INTRADAY_WHAT_TO_SHOW=ADJUSTED_LAST` en `.env` | IBKR `TRADES` = crudo; `ADJUSTED_LAST` = ajustado por dividendos/splits |

En el CSV queda la columna `adj_close` (el precio usado) y
`future_ret_price` indica cuál se aplicó.

---

## 10. Disponibilidad de datos intradía (IBKR)

| Tema | Hallazgo |
|---|---|
| Barras ≤30 seg | solo 6 meses |
| **Barras 1h** | **sin límite de 6 meses**; se piden en chunks de "1 M" |
| Pacing | 60 requests/10 min → 6 meses = 6 requests |
| Antigüedad práctica | 6-12 meses suficientes para RF (880-1.760 filas) |
| Sin conexión IBKR | No se genera nueva barra ni se usa otra fuente |

Comando: `python run_nvda.py --data-only` (requiere TWS o IB Gateway iniciado).

---

## 11. Sentimiento de noticias alineado a barras (implementado)

> Integración con las noticias de Alpha Vantage que trae tu compañero.
> Regla estricta: **para la barra de T solo se usan noticias publicadas
> antes de T** (point-in-time).

### 11.1 Modos de entrada

| Modo | Archivo | Cómo se aplica |
|---|---|---|
| **Artículo-level (recomendado)** | `core/output/news_history/news.csv` o JSON crudo Alpha | Ventanas móviles 6h / 1d / 3d / 7d / 30d + decaimiento exponencial (half-life 12h) |
| **Diario (fallback)** | Ruta configurada en `NEWS_DAILY_PATH` | Se aplica al día siguiente (**lag-1**), nunca al mismo día |

### 11.2 Variables generadas (14)

| Variable | Lectura |
|---|---|
| `sent_w6h` | Sentimiento ponderado de las últimas 6h (reacción inmediata) |
| `sent_w1d` / `sent_w3d` / `sent_w7d` / `sent_w30d` | Ventanas equiv. a los filtros daily/weekly/month del compañero |
| `cnt_w6h` ... `cnt_w30d` | Cantidad de noticias en cada ventana |
| `sent_decay` | Sentimiento ponderado por `e^(−edad/12h)` — continuo, evita ceros |
| `sent_momentum` | `sent_w6h − sent_w1d` (aceleración de la noticia) |
| `hours_since_news` | Horas desde la última noticia (999 = sin noticias) |
| `has_sentiment_24h` | 1 si hubo noticias en 24h |

### 11.3 Esquema que debe entregar tu compañero

```text
published_utc,ticker,relevance_score,ticker_sentiment_score,label,title,url
2026-09-10T14:05:00Z,NVDA,0.83,0.42,Bullish,...
```
Filtros aplicados por el sistema: `relevance_score >= 0.3` y
`ticker == NVDA`. Si solo puede entregar agregados diarios, se usa el
modo lag-1 sin cambios en el modelo.

### 11.4 Test anti-leakage

```bash
python scripts/dev/test_sentiment_alignment.py
```
Verifica: parser JSON, noticia 10:45 no afecta barra 10:30 sí 11:00,
borrar noticias futuras no cambia features de T, y lag-1 diario.

---

*Fuente código:* `PRECIARIO_OIL/core/pattern_detector.py:26 detect_all`, `core/models.py:10 PatternType`, `core/models.py:97 OHLCBar`. Reutilización directa en `PRECIARIO_NVDA/core/pattern_detector.py`.*
