"""Puerta de Riesgo (RiskGate) para control de sizing, límites y cortacircuitos."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tbot.risk.circuit_breakers import CircuitBreakerManager
from tbot.risk.models import AccountMode, RiskDecision, RiskReasonCode
from tbot.risk.ownership import OwnershipLedger
from tbot.risk.sizing import calculate_position_size
from tbot.strategies.interfaces import Signal


class RiskGate:
    """Orquestador central de validación de riesgo previo a la emisión de órdenes."""

    def __init__(
        self,
        account_mode: AccountMode = AccountMode.MARGIN_NO_LEVERAGE,
        risk_per_trade_pct: float = 0.5,
        max_risk_per_trade_pct_hard: float = 1.0,
        max_open_positions: int = 4,
        max_positions_per_cluster: int = 2,
        max_position_notional: Decimal = Decimal("400.0"),
        min_order_notional: Decimal = Decimal("10.0"),
        allow_fractional_bot: bool = True,
        circuit_breaker_mgr: CircuitBreakerManager | None = None,
        ownership_ledger: OwnershipLedger | None = None,
    ) -> None:
        self.account_mode = account_mode
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_risk_per_trade_pct_hard = max_risk_per_trade_pct_hard
        self.max_open_positions = max_open_positions
        self.max_positions_per_cluster = max_positions_per_cluster
        self.max_position_notional = max_position_notional
        self.min_order_notional = min_order_notional
        self.allow_fractional_bot = allow_fractional_bot

        self.circuit_breakers = circuit_breaker_mgr or CircuitBreakerManager()
        self.ownership = ownership_ledger or OwnershipLedger()

    def evaluate(
        self,
        signal: Signal,
        bot_equity: Decimal,
        available_buying_power: Decimal,
        open_positions: dict[str, Any],
        clusters_map: dict[str, str] | None = None,
        proxy_price: Decimal | None = None,
        size_multiplier: float = 1.0,
        unsettled_cash: Decimal = Decimal("0.0"),
        kill_switch_active: bool = False,
    ) -> RiskDecision:
        """Evalúa una señal contra todas las restricciones de riesgo y genera el dimensionamiento."""
        # 1. Kill switch
        if kill_switch_active:
            return RiskDecision(
                approved=False,
                reason_code=RiskReasonCode.REJECT_KILL_SWITCH,
                detail="Kill Switch de emergencia activo en el sistema.",
            )

        # 2. Cortacircuitos
        if not self.circuit_breakers.can_open_new_positions():
            return RiskDecision(
                approved=False,
                reason_code=RiskReasonCode.REJECT_CIRCUIT_BREAKER,
                detail=f"Cortacircuitos activo ({self.circuit_breakers.state.value}).",
            )

        # 3. Ownership / Exclusividad
        if not self.ownership.can_bot_trade(signal.symbol):
            return RiskDecision(
                approved=False,
                reason_code=RiskReasonCode.REJECT_OWNERSHIP_CONFLICT,
                detail=f"El símbolo {signal.symbol} pertenece a operativa manual.",
            )

        # 4. Límite de posiciones abiertas
        if len(open_positions) >= self.max_open_positions:
            return RiskDecision(
                approved=False,
                reason_code=RiskReasonCode.REJECT_MAX_POSITIONS,
                detail=f"Límite máximo de {self.max_open_positions} posiciones alcanzado.",
            )

        # 5. Posición existente en el mismo símbolo
        if signal.symbol in open_positions:
            return RiskDecision(
                approved=False,
                reason_code=RiskReasonCode.REJECT_MAX_POSITIONS,
                detail=f"Ya existe una posición abierta en {signal.symbol}.",
            )

        # 6. Límite por cluster
        clusters = clusters_map or {}
        sig_cluster = clusters.get(signal.symbol)
        if sig_cluster:
            cluster_count = sum(1 for sym in open_positions if clusters.get(sym) == sig_cluster)
            if cluster_count >= self.max_positions_per_cluster:
                return RiskDecision(
                    approved=False,
                    reason_code=RiskReasonCode.REJECT_CLUSTER_LIMIT,
                    detail=f"Límite de cluster '{sig_cluster}' ({self.max_positions_per_cluster}) alcanzado.",
                )

        # 7. Regla de cuenta Cash vs Margin (sin apalancamiento)
        effective_buying_power = available_buying_power
        if self.account_mode == AccountMode.CASH and unsettled_cash > Decimal("0"):
            # En modo Cash, solo fondos liquidados (T+1)
            effective_buying_power = max(Decimal("0.0"), available_buying_power - unsettled_cash)
            if effective_buying_power < self.min_order_notional:
                return RiskDecision(
                    approved=False,
                    reason_code=RiskReasonCode.REJECT_UNSETTLED_CASH,
                    detail="Efectivo no liquidado (T+1) insuficiente para nueva orden sin incurrir en violación.",
                )

        # 8. Cálculo de Sizing determinista
        sizing = calculate_position_size(
            signal_symbol=signal.symbol,
            entry_price=signal.entry_price_ref,
            stop_price=signal.stop_price,
            take_profit_price=signal.take_profit_price,
            bot_equity=bot_equity,
            available_buying_power=effective_buying_power,
            risk_per_trade_pct=self.risk_per_trade_pct,
            max_risk_per_trade_pct_hard=self.max_risk_per_trade_pct_hard,
            size_multiplier=size_multiplier,
            allow_fractional_bot=self.allow_fractional_bot,
            max_position_notional=self.max_position_notional,
            min_order_notional=self.min_order_notional,
            proxy_price=proxy_price,
        )

        if sizing is None:
            if signal.entry_price_ref <= signal.stop_price:
                return RiskDecision(
                    approved=False,
                    reason_code=RiskReasonCode.REJECT_INVALID_STOP,
                    detail="El precio stop no es inferior al precio de entrada.",
                )
            return RiskDecision(
                approved=False,
                reason_code=RiskReasonCode.REJECT_INSUFFICIENT_BUYING_POWER,
                detail="Capital o poder de compra insuficiente para cumplir el tamaño mínimo de orden.",
            )

        return RiskDecision(
            approved=True,
            reason_code=RiskReasonCode.APPROVED,
            detail=f"Aprobado {sizing.path} {sizing.shares} acciones en {sizing.execution_symbol} (riesgo ${sizing.risk_usd}).",
            sizing=sizing,
        )
