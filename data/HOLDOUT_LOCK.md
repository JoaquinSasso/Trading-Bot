# HOLDOUT_LOCK: Especificación de Sellado de Datos y Guardas de Ejecución (R0)

```yaml
schema_version: "1.0.0"
status: "SEALED"
effective_date: "2026-09-21"
governing_rule: "R0 (Sellado Estricto de Holdout y Guardas de Ejecución)"
governing_audit: "Auditoría Externa Cuantitativa v2.2 (Remediación F-19 & PBO = 84.45%)"
hash_algorithm: "SHA-256"
partitions:
  daily_holdout:
    status: "SEALED"
    start_date: "2023-01-01"
    end_date: "2026-02-27"
    resolution: "daily"
    access_rule: "STRICTLY_PROHIBITED_UNTIL_PHASE_6"
  daily_development:
    status: "UNRESTRICTED"
    start_date: "2010-01-01"
    end_date: "2022-12-31"
    resolution: "daily"
    access_rule: "PERMITTED_FOR_RESEARCH_CALIBRATION_AND_CV"
  hourly_holdout:
    status: "SEALED"
    start_timestamp: "2025-09-22 00:00:00 America/New_York"
    end_timestamp: "2026-09-21 23:59:59 America/New_York"
    resolution: "hourly"
    access_rule: "STRICTLY_PROHIBITED_UNTIL_PHASE_6"
  hourly_development:
    status: "UNRESTRICTED"
    start_timestamp: "2023-10-23 00:00:00 America/New_York"
    end_timestamp: "2025-09-21 23:59:59 America/New_York"
    resolution: "hourly"
    access_rule: "PERMITTED_FOR_INTRADAY_DEVELOPMENT"
  intraday_5m_sample:
    status: "RESTRICTED_NON_HOLDOUT"
    start_timestamp: "2026-06-26 09:30:00 America/New_York"
    end_timestamp: "2026-09-21 16:00:00 America/New_York"
    resolution: "5m"
    sessions_count: 60
    access_rule: "DIAGNOSTIC_PLUMBING_ONLY_INELIGIBLE_FOR_MODEL_SELECTION"
```

---

## 1. Justificación y Principio de Integridad Científica

La auditoría externa cuantitativa v2.2 identificó una probabilidad de sobreajuste de backtest (**PBO = 84.45%**) y un Sharpe observado de 1.11, inferior al umbral crítico institucional $SR^* = 1.22$. Asimismo, el hallazgo crítico **F-19** demostró que las simulaciones 2010–2026 sobre el universo S5 sufrían de sesgo retrospectivo (*survivorship and hindsight bias*), invalidándolas como prueba genuina fuera de muestra.

Para restablecer la credibilidad estadística del sistema Trading-Bot, la **Regla 0 (R0)** impone una cuarentena criptográfica y programática absoluta sobre las particiones de holdout. La validez de cualquier modelo predictivo depende de que los datos de prueba permanezcan completamente inaccesibles durante la fase de formulación, selección y ajuste de hiperparámetros.

**Invariante de Sellado:**
Queda terminantemente prohibido cargar, iterar, evaluar, graficar o citar métricas derivadas de los períodos de holdout sellados durante los Milestones M0 a M5. Cualquier transgresión invalida de forma irrevocable el proceso de desarrollo.

---

## 2. Manifiesto Criptográfico de Datos (SHA-256)

Para prevenir la contaminación de datos o alteraciones no documentadas en los archivos base, se certifican los siguientes hashes criptográficos calculados al momento del sellado:

| Archivo de Datos | Período Contenido | Filas | Tamaño (bytes) | Hash SHA-256 |
| :--- | :--- | :--- | :--- | :--- |
| `data/historical_2010_2026/SPY_daily.csv` | 2010-01-04 a 2026-02-27 | 4,065 | 393,222 | `60B90926A17790DF490A0A33C1BD2236654801BDC190E76577E3F4706E082E5D` |
| `data/historical_2020_2022/SPY_daily.csv` | 2020-01-02 a 2022-12-30 | 756 | 44,870 | `C371CCFC3365BD7611AB6E4211971D5BDDE2E6BE6D41AB03D3F6F61D6853A0B2` |
| `data/universe_a/SPY_daily.csv` | 2018-06-01 a 2026-02-27 | 1,948 | 102,711 | `3DFC251512252962A54BCF9850F44E6DD970B6DBEF3F4F5AE6D009E21CCEFAA4` |
| `data/intraday_1h/SPY_1h.csv` | 2023-10-23 a 2026-09-21 | 5,079 | 666,784 | `C67CCD5617FAC78C4C23BA300E3F051A637CB8B36FCA10B89127B02DB37AA33B` |
| `data/intraday_5m/SPY_5m.csv` | 2026-06-26 a 2026-09-21 | 4,634 | 405,625 | `D42D9BA81FCC821F8BA2846412D8AFE0BD364EB1DC9EAC44F5EE1192B495377B` |
| `data/risk_free_rate_bil_2010_2026.csv` | 2010-01-04 a 2026-02-27 | 4,065 | 310,574 | `5D35DFCB50F8E5566FE03E9D492B8464C9566F2D13E11C90EF7A53F12C583EE1` |

---

## 3. Delimitación Exacta de Particiones

### 3.1 Partición Diaria (Resolución `daily` / `1d`)

- **Ventana de Desarrollo Diaria (En-Muestra / Permisiva):**
  - **Inicio:** `2010-01-01` (Primera sesión bursátil: `2010-01-04`).
  - **Fin:** `2022-12-31` (Última sesión bursátil: `2022-12-30`).
  - **Alcance:** 13 años calendario (3.272 sesiones de mercado).
  - **Uso Autorizado:** Búsqueda y calibración de estrategias (S5, Universo A), ingeniería de factores, cross-validation, cálculo de matriz de covarianza, optimización de parámetros preinscritos en `docs/LEDGER.md`.
- **Ventana de Holdout Diaria (Fuera de Muestra / Cuarentena Estricta):**
  - **Inicio:** `2023-01-01` (Primera sesión bursátil: `2023-01-03`).
  - **Fin:** `2026-02-27` (Última sesión disponible).
  - **Alcance:** 38 meses completos (791 sesiones de mercado).
  - **Estado:** **ESTRICTAMENTE SELLADO.** Prohibido su procesamiento hasta la Fase 6 (Milestone M6).

### 3.2 Partición Horaria (Resolución `hourly` / `1h`)

- **Ventana de Desarrollo Horaria (En-Muestra / Permisiva):**
  - **Inicio:** `2023-10-23 00:00:00 America/New_York` (Primer barra: `2023-10-23 09:30:00`).
  - **Fin:** `2025-09-21 23:59:59 America/New_York` (Última barra bursátil: `2025-09-19 15:30:00`).
  - **Alcance:** ~23 meses (3.339 barras horarias en `SPY_1h.csv`).
  - **Uso Autorizado:** Calibración y benchmarking de estrategias intradiarias S6 y S8, evaluación de ganancias PID ($K_p=K_i=K_d=1$).
- **Ventana de Holdout Horaria (Fuera de Muestra / Cuarentena Estricta):**
  - **Inicio:** `2025-09-22 00:00:00 America/New_York` (Primer barra: `2025-09-22 09:30:00`).
  - **Fin:** `2026-09-21 23:59:59 America/New_York` (Última barra: `2026-09-21 13:02:35`).
  - **Alcance:** Exactamente 12 meses (1.740 barras horarias en `SPY_1h.csv`).
  - **Estado:** **ESTRICTAMENTE SELLADO.** Bloqueado hasta la Fase 6.

### 3.3 Muestra Intradiaria de 5 Minutos (60 Sesiones)

- **Límites:** `2026-06-26 09:30:00` a `2026-09-21 16:00:00 America/New_York` (4.634 barras en `SPY_5m.csv`).
- **Clasificación:** **Muestra Única de Diagnóstico de Ejecución (Non-Holdout).**
- **Cláusula de Inelegibilidad para Selección:**
  - Esta muestra existe exclusivamente como arnés de regresión para verificar la plomería de ejecución y correcciones de defectos: reinicio de estado entre sesiones (B-01), redistribución de horizontes disponibles (B-02), eliminación de doble normalización en aceleración (B-04) y factor de escala $\sqrt{78}$ para expansión de volatilidad (B-05).
  - **Prohibición Expresa:** Está estrictamente prohibido utilizar estas 60 sesiones para elegir entre modelos, optimizar ponderaciones de horizonte, sintonizar parámetros o justificar la superioridad empírica de una configuración sobre otra.

---

## 4. Especificación Técnica de las Guardas de Ejecución (`guards.py`)

### 4.1 Firma e Interfaz del Contrato

```python
class HoldoutViolationError(RuntimeError):
    """Lanzada cuando una solicitud de datos o simulación invade una partición de holdout sellada."""
    pass

def assert_not_holdout(
    start: date | datetime | str,
    end: date | datetime | str,
    resolution: str = "daily",
    caller_context: str | None = None,
) -> None:
    """Verifica formalmente que el rango [start, end] no intersecte la ventana de holdout.
    
    Lanza HoldoutViolationError de forma inmediata si existe solapamiento y el holdout
    no ha sido formalmente desellado bajo el protocolo de Fase 6.
    """
```

### 4.2 Lógica Matemática de Intersección

Para dos intervalos cerrados $[S, E]$ (solicitado) y $[H_{start}, H_{end}]$ (holdout):
1. Validación de orden: Si $S > E$, se lanza `ValueError("start date must be <= end date")`.
2. Detección de solapamiento:
   $$\text{Solapamiento} \iff \max(S, H_{start}) \le \min(E, H_{end})$$
   Equivalentemente: $S \le H_{end} \land E \ge H_{start}$.
3. Si $\text{Solapamiento} = \text{Verdadero}$:
   - Si `is_phase6_authorized() == False`: Registrar incidente en `logs/holdout_audit.log` y lanzar `HoldoutViolationError`.
   - Si `is_phase6_authorized() == True`: Registrar acceso autorizado único en `logs/holdout_audit.log` y permitir ejecución.

### 4.3 Puntos de Integración Obligatoria

1. **`backend/tbot/backtest/data_loader.py`:**
   - `HistoricalDataLoader.load_from_csv`: invocar `assert_not_holdout` o filtrar defensivamente filas que invadan holdout.
   - `HistoricalDataLoader.fetch_and_save_real_data`: bloquear descargas públicas que intersecten holdout.
2. **`backend/tbot/backtest/engine.py` (`BacktestEngine` / `ReplayEngine`):**
   - Validar las fechas mínima y máxima de `historical_daily` e `historical_intraday` antes de instanciar el loop de simulación.
3. **Scripts de Ejecución y Benchmarking (`scripts/*.py`):**
   - Todo script que cargue datasets históricos debe pasar por la guarda antes de procesar señales.

---

## 5. Protocolo de Apertura de Holdout en Fase 6 (Milestone M6)

La apertura del holdout es un proceso irrevocable de **un solo uso (one-shot evaluation)**. El holdout no es un segundo entorno de desarrollo; es la auditoría final de la hipótesis nula.

### 5.1 Lista de Pre-Requisitos Estrictos (Checklist Obligatorio)

Para autorizar el desellado del holdout, deben cumplirse **todos y cada uno** de los siguientes requisitos:

- [ ] **M0 Completado:** Guardas activas en `guards.py` y `data_loader.py` con 100% de tests pasando.
- [ ] **M1 Completado:** Defectos B-01 a B-10 corregidos con tests de regresión dedicados pasando en verde; los 1.201 tests existentes preexistentes en verde.
- [ ] **M2 Completado:** Motor unificado en `engine.py`, cortacircuitos de `circuit_breakers.py` cableados, scripts refactorizados a configuraciones declarativas, y reporte `reports/engine_unification.md` aprobado.
- [ ] **M3 Completado:** S5 normalizado (Top-4, cap 25%, lookback 45d `iloc[-46]`, max_holding 30 sesiones, docstrings saneados) y evaluado en las 5 configuraciones de Universo A sobre datos de desarrollo (reporte `reports/s5_universe_a_30d.md`).
- [ ] **M4 Completado:** Evaluación horaria en ventana de desarrollo 2023-10-23 a 2025-09-21 concluida; reportes `reports/overnight_vs_intraday_decomposition.md` y `docs/INTRADAY_DATA_SOURCES.md` finalizados.
- [ ] **M5 Completado:** Todas las hipótesis evaluadas preinscritas en `docs/LEDGER.md` ($N \ge 45$), Sharpe Deflactado (DSR) calculado y PBO recalculado formalmente comprobando que $PBO < 84.45\%$.
- [ ] **M_E2E Completado:** Los 4 niveles de pruebas end-to-end completadas y firmadas en `TEST_READY.md`.
- [ ] **Congelamiento de Configuración (Frozen Model):** Commit hash exacto de Git registrado; parámetros de estrategia, costos y universo sellados sin variables libres.

### 5.2 Mecanismo de Autorización Unidireccional

1. El desellado requiere la presencia de la variable de entorno:
   `TBOT_UNSEAL_HOLDOUT=PHASE6_FINAL_EXECUTION_AUTHORIZED`
2. Debe agregarse un registro firmado en la Sección 7 de este documento (`HOLDOUT_LOCK.md`) especificando:
   - Commit Git congelado.
   - Fecha y hora UTC.
   - Configuración objetivo de `docs/LEDGER.md`.
   - Firma del Agente / Usuario responsable.
3. Se permite **una única ejecución** del motor unificado sobre el rango de holdout.
4. Los resultados íntegros (retornos, Sharpe con tasa BIL dinámica, alpha OLS con SE y $p$-valor, máximo drawdown, operaciones y liquidaciones por cortacircuitos) se volcarán directamente en `reports/final_holdout.md`.
5. **Prohibición Post-Apertura:** Si el resultado en el holdout no alcanza los objetivos esperados, **queda terminantemente prohibido ajustar parámetros y repetir el backtest**. Cualquier ajuste posterior al desellado constituiría p-hacking retroactivo.

---

## 6. Registro de Auditoría y Detección de Transgresiones

El sistema mantendrá un registro estructurado continuo en `logs/holdout_audit.log` con el siguiente esquema JSON:

```json
{
  "timestamp": "2026-09-21T18:45:00Z",
  "event": "HOLDOUT_ACCESS_ATTEMPT",
  "status": "BLOCKED_VIOLATION",
  "resolution": "daily",
  "requested_start": "2024-01-01",
  "requested_end": "2024-12-31",
  "caller": "scripts.run_backtest_2010_2026:load_and_preprocess_market_data:45",
  "git_commit": "abcdef1234567890",
  "reason": "Requested range [2024-01-01, 2024-12-31] intersects sealed daily holdout [2023-01-01, 2026-02-27]"
}
```

Cualquier intento de evasión (captura de excepción para continuar en silencio, bypass de guards mediante lectura directa no auditada o manipulación de fechas del sistema) se considera una violación crítica de integridad.

---

## 7. Registro de Estado y Firmas

| Event | Fecha (UTC) | Agente / Autor | Estado | Git Commit SHA | Notas / Hash de Salida |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Sellado Inicial (R0)** | 2026-09-21 | `m0_spec_miner` / `orchestrator` | **SEALED** | `5f0d2c1` | Particiones diaria y horaria selladas bajo R0. |
| **Apertura Fase 6** | 2026-09-22 | `Antigravity Orchestrator` | **UNSEALED_PHASE_6_FINAL** | `b08a471` | Prerrequisitos M0-M5 cumplidos (PBO=0.00%, N=50, DSR evaluado). Evaluación final única de modelos congelados CAND-01..04. |
