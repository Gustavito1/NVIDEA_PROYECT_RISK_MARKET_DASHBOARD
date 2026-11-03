# KPIs del Informe — PRECIARIO_NVDA

## 1. Objetivo

Evaluar dos aspectos distintos:

1. **Clasificación:** si el modelo distingue correctamente `BUY`, `HOLD` y `SELL`.
2. **Resultado financiero:** si esas señales habrían generado una estrategia razonable después de costes y riesgo.

No se debe elegir un modelo usando una sola métrica.

## 2. KPIs de clasificación

### KPIs principales

| KPI | Uso en el proyecto | Motivo |
|---|---|---|
| **F1 macro** | KPI principal para comparar modelos | Promedia el F1 de BUY, HOLD y SELL sin favorecer la clase mayoritaria |
| **Matriz de confusión normalizada por fila** | Diagnóstico principal | Muestra qué clase real se confunde con otra |
| **Precision BUY** | KPI operativo | De las señales BUY emitidas, cuántas fueron correctas |
| **Recall BUY** | KPI operativo | De todas las oportunidades BUY reales, cuántas detectó el modelo |
| **Precision/Recall SELL** | Control de riesgo | Evalúa si el modelo detecta correctamente salidas o movimientos bajistas |
| **ROC-AUC OVR** | Calidad de ranking probabilístico | Evalúa si las probabilidades ordenan correctamente las tres clases |

### KPIs secundarios

| KPI | Uso |
|---|---|
| **Accuracy** | Contexto general; nunca debe analizarse sola |
| **Balanced Accuracy** | Alternativa a Accuracy cuando las clases están desbalanceadas |
| **Desviación estándar entre folds** | Estabilidad temporal del modelo |
| **Brier Score o calibración** | Verifica si una probabilidad del 70% ocurre aproximadamente 70% de las veces |

### Por qué F1 macro es el principal

Si `HOLD` es la clase dominante, un modelo que siempre diga `HOLD` puede
obtener una Accuracy aparentemente alta y ser inútil para invertir. F1 macro
obliga al modelo a funcionar razonablemente en las tres clases.

## 3. KPIs financieros

Los KPIs financieros deben calcularse únicamente sobre el período de test o
las predicciones out-of-sample del walk-forward.

| KPI | Fórmula/idea | Importancia |
|---|---|---|
| **Sharpe Ratio** | Retorno promedio ajustado por volatilidad | KPI financiero principal de rendimiento/riesgo |
| **Max Drawdown** | Mayor caída desde un máximo hasta el mínimo posterior | KPI principal de riesgo y pérdida máxima histórica |
| **Sortino Ratio** | Retorno ajustado solo por volatilidad negativa | Mejor que Sharpe cuando interesa el riesgo bajista |
| **Win Rate** | Operaciones ganadoras / total de operaciones | Fácil de interpretar, pero no mide el tamaño de las pérdidas |
| **Profit Factor** | Ganancias brutas / pérdidas brutas | Indica si las ganancias compensan las pérdidas |
| **IC Spearman** | Correlación entre score del modelo y retorno futuro | Mide si la señal ordena correctamente oportunidades |

### KPIs financieros complementarios

- Retorno acumulado y retorno anualizado.
- Calmar Ratio: retorno anualizado / máximo drawdown absoluto.
- Número de operaciones.
- Turnover o cantidad de cambios de posición.
- Retorno promedio por operación.
- Costes acumulados de comisión y slippage.
- Comparación contra `Buy & Hold` de NVDA.

## 4. Definición recomendada del backtest

Para evitar resultados irreales:

```text
Predicción en el cierre de la barra t
        ↓
Ejecución en la apertura de la barra t+1
        ↓
Retorno futuro durante el horizonte definido
```

La evaluación principal debe ser **long-only**:

- `BUY`: abrir o mantener una posición larga.
- `HOLD`: mantener la posición actual o no abrir una nueva.
- `SELL`: cerrar la posición y pasar a efectivo.

Como análisis adicional puede evaluarse una estrategia long/short:

```text
BUY = +1 | HOLD = 0 | SELL = -1
```

Esta segunda alternativa debe identificarse como hipotética porque supone

## 5. Fórmulas relevantes para barras de 1 hora

Si los retornos financieros se calculan por barra horaria RTH:

```text
periodos_anuales ≈ 252 días × 7 barras = 1.764 barras
Sharpe  = promedio(retornos_exceso) / desviacion(retornos) × √1.764
Sortino = promedio(retornos_exceso) / desviacion_negativa(retornos) × √1.764
```

No se debe usar automáticamente `√252` si se están evaluando retornos por

El **IC Spearman** debe usar preferentemente un score continuo:

```text
prediction_score = probabilidad_BUY - probabilidad_SELL
IC = Spearman(prediction_score, retorno_futuro_real)
```

Es mejor que correlacionar solamente las etiquetas discretas.

## 6. Criterio para elegir el modelo

El modelo final se seleccionará con este orden:

1. F1 macro superior al baseline de mayoría.
2. Mejor equilibrio en la matriz de confusión, sin ignorar BUY o SELL.
3. ROC-AUC OVR superior a 0.50 y estable entre folds.
4. Sharpe positivo después de costes.
5. Drawdown aceptable y preferiblemente inferior al benchmark.
6. Si dos modelos son estadísticamente similares, elegir el más simple.

No existe un porcentaje universal que garantice que un modelo sea rentable.
Como guía académica:

| Métrica | Interpretación aproximada |
|---|---|
| ROC-AUC 0.50 | Azar |
| ROC-AUC 0.55–0.60 | Señal débil/modesta |
| ROC-AUC 0.60–0.65 | Señal razonable |
| F1 macro > baseline y estable | El modelo aprende más que la clase mayoritaria |
| Sharpe > 0 después de costes | La estrategia tiene retorno ajustado por riesgo positivo |
| Sharpe muy alto sin estabilidad | Posible overfitting o leakage |

Estos rangos son referencias para interpretar, no reglas financieras
universales.

## 7. KPIs mínimos para el PowerPoint

### Clasificación

1. F1 macro.
2. Matriz de confusión normalizada por fila.
3. Precision BUY y Precision SELL.
4. Recall BUY y Recall SELL.
5. ROC-AUC OVR.
6. Accuracy como métrica secundaria.

### Financieros

1. Sharpe Ratio.
2. Max Drawdown.
3. Sortino Ratio.
4. Win Rate.
5. Profit Factor.
6. IC Spearman.
7. Comparación contra Buy & Hold.

## 8. Visualización recomendada en Power BI

- Tarjetas: F1 macro, ROC-AUC, Precision BUY, Sharpe, Max Drawdown y Win Rate.
- Matriz de confusión normalizada por fila.
- Curva de equity del modelo contra Buy & Hold.
- Curva de drawdown.
- Tabla de predicciones con fecha, señal, probabilidades y retorno realizado.
- Gráfico de importancia de variables del Random Forest o SHAP para XGBoost.

Los valores concretos del modelo se reportarán después de incorporar las
noticias reales y cerrar el backtest out-of-sample.
