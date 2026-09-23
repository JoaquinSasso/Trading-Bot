---
name: joapc-remote-execution
description: >-
  Procedimiento para delegar tareas de backtesting computacionalmente pesadas a la PC de escritorio
  remota (JOAPC / 192.168.0.108) cuando está disponible, con fallback automático a ejecución local.
---

# Ejecución de Backtesting Pesado en JOAPC

## Routing: ¿Remoto o Local?

| Criterio | Decisión |
|---|---|
| Tarea estimada en < ~2 minutos | **Ejecutar local** |
| Tarea estimada en > 2 minutos | **Intentar JOAPC primero → fallback local** |
| JOAPC no responde al check SSH | **Ejecutar local directamente** |

Scripts que van a JOAPC: `optimize_and_benchmark_portfolio.py`, `run_hourly_backtests.py`, `run_final_holdout_evaluation.py`, `run_phase2_institutional_metrics.py`, `run_multifactor_attribution.py`, `recompute_dsr_and_pbo.py`.

Scripts que siempre se ejecutan local: `pytest`, `recompute_rank_monotonicity_newey_west.py`, análisis exploratorios rápidos.

---

## Paso 1 — Verificar Conectividad

```powershell
ssh -n -o ConnectTimeout=5 -o BatchMode=yes 192.168.0.108 "echo JOAPC_OK"
```

- `JOAPC_OK` → continuar.
- Falla → **saltar al Paso 4 (Fallback Local)**.

---

## Paso 2 — Sincronizar Código Hacia JOAPC (scp)

> [!NOTE]
> Los datos en `data/` (+1.2 GB) se sincronizan automáticamente por Syncthing. `scp` solo transfiere el código.

```powershell
scp -r backend "192.168.0.108:D:/Github Repositories/Trading-Bot/"
scp scripts/optimize_and_benchmark_portfolio.py    "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
scp scripts/run_hourly_backtests.py                "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
scp scripts/run_final_holdout_evaluation.py        "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
scp scripts/run_phase2_institutional_metrics.py    "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
scp scripts/run_multifactor_attribution.py         "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
scp scripts/recompute_dsr_and_pbo.py               "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
```

**Forzar rescan de Syncthing en JOAPC antes de ejecutar** (API Key en `http://127.0.0.1:8384` → Actions → Settings):

```powershell
ssh -n 192.168.0.108 powershell -NoProfile -Command `
    "Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8384/rest/db/scan?folder=trading-data' -Headers @{'X-API-Key'='API_KEY_DE_JOAPC'}"
```

---

## Paso 3 — Ejecutar en JOAPC

> [!NOTE]
> Usar siempre `ssh -n` para evitar que OpenSSH de Windows quede colgado esperando EOF.

```powershell
# Benchmark completo de portafolio
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/optimize_and_benchmark_portfolio.py"

# Backtests horarios S6 / S8
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/run_hourly_backtests.py"

# Evaluación holdout final
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/run_final_holdout_evaluation.py"

# Métricas institucionales Fase 2
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/run_phase2_institutional_metrics.py"

# Atribución multifactor
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/run_multifactor_attribution.py"

# Recalcular DSR y PBO
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/recompute_dsr_and_pbo.py"
```

---

## Paso 3b — Traer Reports a Local

Los datos en `data/` llegan solos vía Syncthing. Solo hace falta traer los reports:

```powershell
scp -r "192.168.0.108:D:/Github Repositories/Trading-Bot/reports" .
```

**Forzar rescan en local para recibir los datos actualizados:**

```powershell
Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8384/rest/db/scan?folder=trading-data" `
    -Headers @{ "X-API-Key" = "TU_API_KEY_LOCAL" }
```

---

## Paso 4 — Fallback: Ejecución Local (JOAPC no disponible)

```powershell
python scripts/optimize_and_benchmark_portfolio.py
python scripts/run_hourly_backtests.py
python scripts/run_final_holdout_evaluation.py
python scripts/run_phase2_institutional_metrics.py
```
