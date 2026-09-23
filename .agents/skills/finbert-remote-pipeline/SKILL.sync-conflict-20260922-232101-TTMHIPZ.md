---
name: finbert-remote-pipeline
description: >-
  Guía y procedimiento para ejecutar la ingesta multi-fuente de noticias (SEC EDGAR, Yahoo RSS, Alpaca),
  la inferencia con FinBERT en la PC de escritorio remota (JOAPC) y la evaluación del benchmark de portafolio.
---

# Pipeline Multi-Fuente de Noticias y FinBERT en PC Remota (JOAPC)

Este skill describe el procedimiento estándar para recopilar noticias, procesar sentimientos con FinBERT en la PC de escritorio potente y actualizar las señales cuantitativas del bot de trading.

## 1. Verificación de Conectividad con JOAPC

Verificar que la estación remota (`192.168.0.108`) responde por SSH (utilizar siempre `-n`):

```powershell
ssh -n 192.168.0.108 "echo JOAPC Connected"
```

## 2. Sincronización de Código Hacia JOAPC

Transferir los scripts y módulos actualizados:

```powershell
scp scripts/extract_and_process_historical_news.py "192.168.0.108:D:/Github Repositories/Trading-Bot/scripts/"
scp -r backend "192.168.0.108:D:/Github Repositories/Trading-Bot/"
```

## 3. Ejecución del Pipeline de Noticias y FinBERT en JOAPC

Lanzar la extracción multi-fuente (SEC EDGAR Form 8-K + Yahoo RSS + Cables sectoriales) e inferencia en batch:

```powershell
ssh -n 192.168.0.108 powershell -NoProfile -Command "Set-Location 'D:\Github Repositories\Trading-Bot'; python scripts/extract_and_process_historical_news.py --symbols SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,JPM,LLY,XOM,COST,GLD,SLV --device cpu --batch-size 32"
```

*Nota: Monitorear el progreso. En el procesador Ryzen 7 8700G, el procesamiento de ~7.500 observaciones toma aproximadamente 1 a 2 minutos.*

## 4. Sincronización de Datasets de Vuelta a la Máquina Local

Traer las características cuantitativas generadas y la caché de noticias crudas:

```powershell
scp "192.168.0.108:D:/Github Repositories/Trading-Bot/data/news_features/historical_news_features.csv" data/news_features/
scp -r "192.168.0.108:D:/Github Repositories/Trading-Bot/data/news/raw" data/news/
```

## 5. Validación y Ejecución de Simulación / Benchmark

1. Validar que la suite de tests pasa al 100%:
   ```powershell
   python -m pytest backend/tests/ -q
   ```
2. Ejecutar la simulación completa del portafolio:
   ```powershell
   python scripts/optimize_and_benchmark_portfolio.py
   ```
3. Verificar que la estrategia **S5 v1.2.0 (Multi-Sectorial + Metales)** mantiene sus métricas objetivo:
   - Retorno Anual > +75%
   - Sharpe Ratio > 2.50
   - Max Drawdown < -10%
   - Alpha sobre S&P 500 > +50%
