# Contexto de Inicialización - Proyecto PRECIARIO_NVDA

Bienvenido. Para este chat, DEBES cargar en tu memoria los siguientes módulos antes de proponer cualquier código:

1. **Tu Rol Principal:** Lee `.opencode/agents/AGENT_QUANT_NVDA.md`. Esa es tu personalidad, tus restricciones y tus fórmulas matemáticas.
2. **Auditoría de Código:** Antes de entregar scripts, aplica las reglas de `.opencode/agents/AGENT_CODE_AUDITOR.md` para evitar Data Leakage (fuga de datos del futuro).
3. **Rutas de los datos:**
    - NVDA: `core/output/intradia/NVDA_intraday.csv`
    - SOXQ: `core/output/intradia/SOXQ_intraday.csv`
    - News (noticias): `core/output/news_history/news.csv`
    NOTA: SON DATOS DE OTRO COMPAÑERO QUE TAMBIEN USO IBKR EN SU DOCKER Y OBTUVO DATOS MAS ANTIGUOS

4. **Módulo de Velas (Price Action):** Cuando trabajemos con datos diarios, usa las matemáticas definidas en `.opencode/skills/python/candlestick_math.py`. Prohibido inventar patrones de velas binarios.
5. **Módulo Web:** Para la futura exportación de KPIs a HTML, respeta los patrones de accesibilidad y diseño de `.opencode/skills/web/`.
6. **Contexto Histórico:** El resumen del proyecto y la arquitectura (PostgreSQL en Docker, Power BI) están en `.opencode/context/PROYECT_NVDA.md`.

**Objetivo Inmediato del Chat:** 
Tenemos un problema de etiquetado donde el Baseline de Mayoría es del 18%, el F1 Macro es 40% y el ROC-AUC 56%. 
Tu primera tarea será revisar el script de etiquetado de `future_ret_5d` y ajustar el parámetro `tau` para lograr una distribución balanceada donde la clase HOLD sea aproximadamente el 40-50%, y BUY/SELL sean ~25% cada una.

**Acotaciones:**
1. Yo se que en 2020 y parte del 2021 hubo la pandemia que todos conocemos y al ser un caso excepcional tal vez añada ruido el modelo, asi que analiza esos casos tambien.

2. Si tienes alguna duda preguntamelo antes de realizar el cambio.

3. Todo lo que tenga que ver con docker eliminalo ya que en mi version anterior uso ibkr teneiendo abierto el TWS el cual esta activado actualmente, cuando el modelo funcione al 100% usare docker... por ahora elimina todo lo referente a este.

**Restricciones Históricas y de Sentimiento (CRÍTICO):**
1. Corte Temporal: El dataset de NVDA y Noticias inicia en 2020, pero el ETF SOXQ inició en junio de 2021. Para evitar distorsiones por la pandemia y mantener el cálculo del Alpha relativo, todo el backtest y el feature engineering debe iniciar obligatoriamente desde el 11 de junio de 2021. Descarta los datos anteriores en el pipeline.

2. Sincronización Intradía: Estamos en un entorno Day Trading (velas de 1 hora). Tienes ESTRICTAMENTE PROHIBIDO hacer un merge simple del sentimiento diario de Alpha Vantage sobre las velas horarias, ya que esto inyecta ruido o fuga de datos (Data Leakage).

3. Cálculo Point-in-Time: Las noticias de news.csv deben procesarse usando su marca de tiempo exacta (time_published). Debes generar un feature de sentimiento horario aplicando una media móvil ponderada exponencialmente (Half-Life de 12 horas). Si una noticia sale a las 10:45, su impacto solo debe reflejarse en las velas de las 11:00 en adelante, decayendo gradualmente durante el día.