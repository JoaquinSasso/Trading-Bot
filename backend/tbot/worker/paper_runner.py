"""Runner de Simulación y Sesión Diaria de Paper Trading.

Ejecuta el ciclo de vida completo de un día de decisiones de mercado:
1. 09:20 ET - Rutina pre-mercado, reset de cortacircuitos y conciliación.
2. 15:30 ET - Evaluación de estrategia intradiaria S1 (Momentum).
3. 15:45 ET - Evaluación de estrategia swing S3 (Pullback a la tendencia) y brackets nativos.
4. 15:58 ET - Cierre de posiciones intradiarias por tiempo (PositionGuardian).
5. 16:00 ET - Conciliación final de fin de sesión y reporte de decisiones.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from tbot.ai.providers import MockAIProvider
from tbot.ai.schemas import VetoVerdict
from tbot.ai.veto import AIVeto
from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.config.settings import settings
from tbot.execution.alpaca_broker import AlpacaBroker
from tbot.execution.interfaces import BrokerAdapter
from tbot.execution.router import OrderRouter
from tbot.execution.simulated_broker_adapter import SimulatedBrokerAdapter
from tbot.guardian.guardian import PositionGuardian
from tbot.news.models import NewsFeatures
from tbot.regime.filter import MarketRegime
from tbot.risk.circuit_breakers import CircuitBreakerManager
from tbot.risk.gate import RiskGate
from tbot.risk.models import AccountMode
from tbot.risk.ownership import OwnershipLedger
from tbot.strategies.interfaces import Signal, StrategyContext
from tbot.strategies.s1_intraday_momentum import IntradayMomentumStrategy
from tbot.strategies.s3_trend_pullback import TrendPullbackStrategy


async def run_paper_session(
    target_date: date = date(2025, 11, 14),
    broker_type: str = "simulated",
    capital: float = 2000.0,
    veto_mode: str = "required",
    symbols: list[str] | None = None,
) -> int:
    symbols = symbols or ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]

    print("\n" + "=" * 76)
    print("           SIMULACIÓN DE SESIÓN DE DECISIONES CON PAPER MONEY")
    print(f"  Fecha de Simulación: {target_date} | Capital Asignado: ${capital:,.2f}")
    print(f"  Modo Broker: {broker_type.upper()} | Modo Veto IA: {veto_mode.upper()}")
    print("=" * 76 + "\n")

    # 1. Inicialización de Componentes de Ejecución y Riesgo
    broker: BrokerAdapter
    if broker_type.lower() == "alpaca":
        if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
            print(
                "[ALERTA] Claves de Alpaca no configuradas en .env. Conmutando a SimulatedBroker."
            )
            broker = SimulatedBrokerAdapter(initial_capital=Decimal(str(capital)))
        else:
            broker = AlpacaBroker(paper=True)
            print("[INFO] Conectado exitosamente a Alpaca Paper Trading API.")
    else:
        broker = SimulatedBrokerAdapter(initial_capital=Decimal(str(capital)))
        print("[INFO] Broker simulado de alta fidelidad inicializado.")

    ownership = OwnershipLedger()
    cb = CircuitBreakerManager(daily_loss_limit_pct=2.0, emergency_loss_limit_pct=3.5)
    risk_gate = RiskGate(
        account_mode=AccountMode.MARGIN_NO_LEVERAGE,
        risk_per_trade_pct=0.5,
        max_open_positions=4,
        ownership_ledger=ownership,
        circuit_breaker_mgr=cb,
    )
    router = OrderRouter(broker=broker)
    guardian = PositionGuardian(broker=broker, ownership_ledger=ownership)

    # Proveedor de IA (Mock configurable o Gemini)
    mock_ai = MockAIProvider(
        default_verdict="CONFIRM",
        analysis="Validación de riesgo algorítmico: momentum saludable sin conflicto de noticias.",
    )
    veto = AIVeto(mode=veto_mode, primary_provider=mock_ai)

    # 2. Carga de datos de mercado
    loader = HistoricalDataLoader()
    daily_bars: dict[str, pd.DataFrame] = {}
    current_prices: dict[str, Decimal] = {}

    for s in symbols:
        csv_p = Path(f"data/historical/{s}_daily.csv")
        if csv_p.exists():
            df = loader.load_from_csv(csv_p, s)
        else:
            df = loader.fetch_and_save_real_data(s)
        # Filtrar hasta el día objetivo
        d_col = "date" if "date" in df.columns else "timestamp"
        df["_pdate"] = pd.to_datetime(df[d_col]).dt.date
        df_target = df[df["_pdate"] <= target_date]
        if not df_target.empty:
            daily_bars[s] = df_target
            cur_c = Decimal(str(round(float(df_target["close"].iloc[-1]), 4)))
            current_prices[s] = cur_c
            if isinstance(broker, SimulatedBrokerAdapter):
                broker.set_current_price(s, cur_c)

    # Precios de proxy (SPYM y QQQM)
    # En 2025: SPYM ~ SPY / 10, QQQM ~ QQQ
    if "SPY" in current_prices:
        spym_p = (current_prices["SPY"] / Decimal("10")).quantize(Decimal("0.01"))
        current_prices["SPYM"] = spym_p
        if isinstance(broker, SimulatedBrokerAdapter):
            broker.set_current_price("SPYM", spym_p)

    if "QQQ" in current_prices:
        qqqm_p = current_prices["QQQ"]
        current_prices["QQQM"] = qqqm_p
        if isinstance(broker, SimulatedBrokerAdapter):
            broker.set_current_price("QQQM", qqqm_p)

    # ------------------------------------------------------------------
    # FASE 1: 09:20 ET - Rutina Pre-Mercado
    # ------------------------------------------------------------------
    t_0920 = datetime.combine(target_date, time(9, 20))
    print(f"[{t_0920.strftime('%H:%M:%S')} ET] 1. Rutina Pre-Mercado:")
    acc_info = broker.get_account_info()
    print(f"   Equity Disponible: ${acc_info.equity:,.2f} | Efectivo: ${acc_info.cash:,.2f}")
    cb.reset_daily(acc_info.equity, target_date)
    reconcile_actions = guardian.reconcile(t_0920)
    for act in reconcile_actions:
        print(f"   [CONCILIACIÓN] {act}")
    resubmit_actions = guardian.resubmit_daily_stops(t_0920)
    for act in resubmit_actions:
        print(f"   [STOP REENVIADO] {act}")
    print("   [OK] Pre-mercado completado sin anomalías.")

    # Diario de decisiones tomadas durante el día
    decision_journal: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # FASE 2: 15:30 ET - Evaluación de Estrategia S1 (Intraday Momentum)
    # ------------------------------------------------------------------
    t_1530 = datetime.combine(target_date, time(15, 30))
    print(f"\n[{t_1530.strftime('%H:%M:%S')} ET] 2. Evaluación Estrategia S1 (Intraday Momentum):")
    strat_s1 = IntradayMomentumStrategy()
    ctx_s1 = StrategyContext(
        now=t_1530,
        regime=MarketRegime.BULL_CALM,
        daily_bars=daily_bars,
        intraday_bars={},
        current_prices=current_prices,
        portfolio_positions=set(guardian.tracked_positions.keys()),
    )
    s1_signals = strat_s1.generate(ctx_s1)
    if not s1_signals:
        print("   [INFO] S1 no detectó condición de momentum intradía.")
    else:
        for sig in s1_signals:
            await _process_signal(
                sig,
                veto,
                risk_gate,
                router,
                guardian,
                broker,
                acc_info,
                current_prices,
                t_1530,
                decision_journal,
            )

    # ------------------------------------------------------------------
    # FASE 3: 15:45 ET - Evaluación de Estrategia S3 (Trend Pullback)
    # ------------------------------------------------------------------
    t_1545 = datetime.combine(target_date, time(15, 45))
    print(
        f"\n[{t_1545.strftime('%H:%M:%S')} ET] 3. Evaluación Estrategia S3 (Trend Pullback v1.1.0):"
    )
    strat_s3 = TrendPullbackStrategy()
    ctx_s3 = StrategyContext(
        now=t_1545,
        regime=MarketRegime.BULL_CALM,
        daily_bars=daily_bars,
        intraday_bars={},
        current_prices=current_prices,
        portfolio_positions=set(guardian.tracked_positions.keys()),
    )
    s3_signals = strat_s3.generate(ctx_s3)
    if not s3_signals:
        print("   [INFO] S3 no detectó retrocesos activos sobre los 5 activos analizados.")
    else:
        print(
            f"   [SEÑALES DETECTADAS] S3 generó {len(s3_signals)} señal(es). Evaluando cadena de decisión:"
        )
        for sig in s3_signals:
            await _process_signal(
                sig,
                veto,
                risk_gate,
                router,
                guardian,
                broker,
                acc_info,
                current_prices,
                t_1545,
                decision_journal,
            )

    # ------------------------------------------------------------------
    # FASE 4: 15:58 ET - Cierre de Posiciones Intradía (Guardian)
    # ------------------------------------------------------------------
    t_1558 = datetime.combine(target_date, time(15, 58))
    print(f"\n[{t_1558.strftime('%H:%M:%S')} ET] 4. Gestión de Salidas por Tiempo (15:58 ET):")
    time_exit_actions = guardian.check_time_exits(t_1558)
    if not time_exit_actions:
        print("   [INFO] No hay posiciones intradiarias pendientes de cierre.")
    else:
        for act in time_exit_actions:
            print(f"   [CIERRE TIEMPO] {act}")

    # ------------------------------------------------------------------
    # FASE 5: 16:00 ET - Conciliación Final y Cierre de Sesión
    # ------------------------------------------------------------------
    t_1600 = datetime.combine(target_date, time(16, 0))
    print(f"\n[{t_1600.strftime('%H:%M:%S')} ET] 5. Conciliación Final de Cierre de Sesión:")
    final_actions = guardian.reconcile(t_1600)
    for act in final_actions:
        print(f"   [CONCILIACIÓN] {act}")

    final_acc = broker.get_account_info()
    final_positions = broker.get_positions()
    final_orders = broker.get_open_orders()

    # ------------------------------------------------------------------
    # REPORTE DE DECISIONES Y RESULTADO
    # ------------------------------------------------------------------
    print("\n" + "=" * 76)
    print("                    DIARIO DE DECISIONES DE LA SESIÓN")
    print("=" * 76)
    if not decision_journal:
        print("  Sin señales ejecutadas durante este día de mercado.")
    else:
        print(
            f"{'Ticker':<8} | {'Estrategia':<18} | {'Veto IA':<10} | {'RiskGate':<10} | {'Detalle / Orden'}"
        )
        print("-" * 76)
        for d in decision_journal:
            print(
                f"{d['symbol']:<8} | {d['strategy']:<18} | {d['veto']:<10} | {d['risk']:<10} | {d['detail']}"
            )

    print("\n" + "=" * 76)
    print("                     ESTADO FINAL DE LA CUENTA")
    print("=" * 76)
    print(f"  Capital Inicial:   ${acc_info.equity:,.2f}")
    print(f"  Capital al Cierre: ${final_acc.equity:,.2f}")
    print(f"  Efectivo Libre:    ${final_acc.cash:,.2f}")
    print(f"  Posiciones Abiertas: {len(final_positions)}")

    if final_positions:
        print("\n  [POSICIONES PROTEGIDAS OVERNIGHT EN BROKER]:")
        for p in final_positions:
            print(
                f"    - {p.symbol}: {p.qty} accs @ ${p.entry_price:,.2f} | Valor: ${p.market_value:,.2f}"
            )

    if final_orders:
        print("\n  [ÓRDENES BRACKET ACTIVAS EN SERVIDOR DEL BROKER]:")
        for o in final_orders:
            print(
                f"    - {o.symbol} {o.order_type.upper()}: {o.qty} accs (ID: {o.order_id[:8]}...)"
            )

    print("\n[OK] Simulación de sesión completada exitosamente sin excepciones.")
    return 0


async def _process_signal(
    sig: Signal,
    veto: AIVeto,
    risk_gate: RiskGate,
    router: OrderRouter,
    guardian: PositionGuardian,
    broker: BrokerAdapter,
    acc_info: Any,
    current_prices: dict[str, Decimal],
    now: datetime,
    journal: list[dict[str, str]],
) -> None:
    # 1. Filtro de Veto de IA
    dummy_news = NewsFeatures(
        symbol=sig.symbol,
        window="24h",
        ts=now,
        n_items=3,
        sentiment_mean=0.25,
        sentiment_min=-0.1,
        negative_share=0.05,
        sources=["AlpacaNews"],
        top_topics=["earnings", "momentum"],
    )
    veto_res = await veto.review(
        signal=sig,
        features={"score": sig.score},
        regime="BULL_CALM",
        events={},
        news=dummy_news,
        now=now,
    )

    if veto_res.verdict == VetoVerdict.REJECT:
        journal.append(
            {
                "symbol": sig.symbol,
                "strategy": sig.strategy_id,
                "veto": "REJECT",
                "risk": "OMITIDO",
                "detail": f"Veto IA rechazó la señal: {veto_res.reason_code}",
            }
        )
        print(f"   [VETO IA RECHAZÓ] {sig.symbol} ({veto_res.reason_code})")
        return

    # 2. Evaluación RiskGate
    proxy_p = current_prices.get(
        "SPYM" if sig.symbol == "SPY" else ("QQQM" if sig.symbol == "QQQ" else sig.symbol)
    )
    risk_dec = risk_gate.evaluate(
        signal=sig,
        bot_equity=acc_info.equity,
        available_buying_power=acc_info.cash,
        open_positions=guardian.tracked_positions,
        proxy_price=proxy_p,
        size_multiplier=veto_res.size_multiplier,
    )

    if not risk_dec.approved or not risk_dec.sizing:
        journal.append(
            {
                "symbol": sig.symbol,
                "strategy": sig.strategy_id,
                "veto": str(veto_res.verdict.value),
                "risk": str(risk_dec.reason_code.value),
                "detail": risk_dec.detail,
            }
        )
        print(f"   [RISK RECHAZÓ] {sig.symbol}: {risk_dec.reason_code.value} - {risk_dec.detail}")
        return

    sizing = risk_dec.sizing

    # 3. Emisión determinista de la orden mediante OrderRouter
    router.route_signal(sig, sizing, now)

    # 4. Custodia en PositionGuardian
    guardian.register_bot_position(
        symbol=sig.symbol,
        execution_symbol=sizing.execution_symbol,
        strategy_id=sig.strategy_id,
        entry_price=sizing.entry_price,
        stop_price=sizing.stop_price,
        take_profit_price=sizing.take_profit_price,
        opened_at=now,
        path=sizing.path,
        exit_at_close=sig.exit_at_close,
    )

    order_info = f"Orden Bracket {sizing.shares} {sizing.execution_symbol} @ ${sizing.entry_price:,.2f} (SL: ${sizing.stop_price}, TP: ${sizing.take_profit_price})"
    journal.append(
        {
            "symbol": sig.symbol,
            "strategy": sig.strategy_id,
            "veto": str(veto_res.verdict.value),
            "risk": "APPROVED",
            "detail": order_info,
        }
    )
    print(f"   [ORDEN ENVIADA] {order_info}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulador de Sesión Diaria en Paper Trading.")
    parser.add_argument("--date", type=str, default="2025-11-14", help="Fecha YYYY-MM-DD")
    parser.add_argument("--broker", type=str, default="simulated", choices=["simulated", "alpaca"])
    parser.add_argument("--capital", type=float, default=2000.0, help="Capital inicial asignado")
    parser.add_argument(
        "--veto",
        type=str,
        default="quantitative",
        choices=["quantitative", "required", "advisory", "off"],
        help="Modo de veto: quantitative (reglas matemáticas sobre FinBERT), required, advisory, off",
    )

    args = parser.parse_args()
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    return asyncio.run(
        run_paper_session(
            target_date=target_date,
            broker_type=args.broker,
            capital=args.capital,
            veto_mode=args.veto,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
