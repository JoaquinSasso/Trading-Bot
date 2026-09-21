# NOTA INSTITUCIONAL DE CABLEADO Y DESACOPLAMIENTO DE FINBERT (T-08)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-08: Nota de Cableado y Desacoplamiento de FinBERT |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgo F-17 y Decisión D-01 de `AUDIT_FOLLOWUP_v2.0.md` |
| **Estado** | **Aprobado y Ejecutado (D-01)** |

---

## 1. Contexto y Análisis Forense del Hallazgo F-17

En la auditoría v2.0, el auditor observó una aparente inconsistencia aritmética:
> *"Con una densidad de ~1 observación por activo-día, una única noticia negativa produce `negative_share = 1.0`, que supera el umbral de 0.35 con amplio margen. Bajo esa aritmética el veto debería activarse en el orden del 20-40% de los días con noticia, no en 16 de 7.490 observaciones (0.2%)."*

La investigación forense sobre la base de datos `historical_news_features.parquet` y el código de agregación (`scripts/extract_and_process_historical_news.py`, `tbot/news/aggregator.py` y `tbot/news/store.py`) confirma la causa raíz exacta de esta discrepancia:

### 1.1. Estructura de la Muestra (Denominador Real)
- La cifra de **7.490 filas** en el dataset no representa 7.490 artículos o eventos de noticias.
- Representa una **malla temporal regular** generada a las 15:45 ET para cada uno de los 14 activos a lo largo de los días hábiles del período muestreado (14 activos × ~535 días hábiles ≈ 7.490 observaciones).
- En el **87.8% de esas observaciones**, no hubo ninguna noticia publicada para ese activo en la ventana de 24 horas (`n_items = 0`).
- Por diseño del método `NewsFeatures.empty(...)`, en ausencia de noticias el valor devuelto es `negative_share = 0.0`. Por tanto, ~6.575 observaciones tenían por definición `negative_share == 0.0`, reduciendo el universo efectivo con noticias activas a solo ~915 activo-días.

### 1.2. Embudo de Filtros y Condición de Evaluación
El veto de FinBERT no se evaluó de forma aislada sobre todos los activo-días con noticias. En el pipeline de ejecución (`ablation_finbert_test.py` y `paper_runner.py`), el veto se evaluó **únicamente después** de que un activo superara los siguientes filtros secuenciales:
1. **Filtro de Régimen Macro:** `SPY` por encima de la EMA50 (excluyendo automáticamente semanas bajistas de pánico masivo donde las noticias negativas son abundantes).
2. **Filtro de Tendencia Micro:** `cur_close >= EMA(20)`.
3. **Filtro de Momentum Positivo:** Retorno a 45 días $> 0$.
4. **Filtro de Portafolio:** El activo no debe estar ya en la cartera (`sym not in open_positions`).

### 1.3. Conclusión sobre la Baja Tasa de Activación
Los activos que se encuentran en clara tendencia alcista (`close > EMA20`) y con momentum positivo a 45 días exhiben una probabilidad condicional sumamente baja de registrar simultáneamente un clúster de noticias negativas en las últimas 24 horas. Por tanto:
- El veto sí se ejecutó correctamente sobre los candidatos elegibles, pero el conjunto de candidatos ya venía fuertemente purgado por la selección de momentum.
- La afirmación previa de que *"el momentum ya hace el trabajo del NLP"* era una hipótesis causal no demostrada experimentalmente, tal como señaló con precisión el auditor en F-17.
- Sin embargo, operativamente, mantener una infraestructura compleja (GPU remota vía SSH, ingesta continua de SEC EDGAR, modelos de lenguaje pesados) para vetar 0 a 16 candidatos en 5 años introduce una **superficie de fallo y latencia asimétrica** que no compensa el beneficio empírico.

---

## 2. Decisión D-01: Retiro Permanente de FinBERT del Camino Crítico

En estricto cumplimiento de la resolución **D-01**:

1. **Camino Crítico de Producción Desacoplado:**
   - Se elimina la dependencia obligatoria de FinBERT, PyTorch y transformers del motor de toma de decisiones en tiempo real y paper trading.
   - El runner de paper trading (`backend/tbot/worker/paper_runner.py`) operará por defecto en modo `veto_mode="off"`, evaluando directamente la lógica determinista cuantitativa.
   - Se elimina el requisito de túneles SSH y conexión activa a la máquina remota GPU (`JOAPC`) para la operativa de trading.

2. **Reclasificación como Telemetría Offline:**
   - El pipeline de ingesta de SEC EDGAR y procesamiento NLP queda archivado exclusivamente como módulo de investigación offline y análisis forense post-mortem.
   - No participará en la emisión, modificación o veto de órdenes de mercado en producción.

3. **Invariantes Actualizados en `GEMINI.md`:**
   - Las decisiones de trading son 100% deterministas basadas en precio y volumen.
   - Ningún modelo de NLP ni LLM interviene en el envío o veto de órdenes en el flujo de ejecución activo.
