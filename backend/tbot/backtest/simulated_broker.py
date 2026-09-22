"""Broker simulado para backtesting y replay offline.

Modela la ejecución realista de órdenes según las reglas del plan maestro:
- Fills a mercado en apertura de la siguiente barra con slippage diferenciado (2 bps ETFs, 5 bps acciones).
- Ejecución de stops al peor precio ante gaps nocturnos o intradiarios (max(open, stop) o min(open, stop)).
- Fills de órdenes limit solo si el precio cruza el nivel.
- Deducción precisa de tarifas regulatorias en ventas (SEC fee + FINRA TAF).
- Seguimiento riguroso de efectivo, posiciones y trades cerrados con P&L en USD y en R.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from tbot.strategies.interfaces import Signal


@dataclass
class UnsettledCredit:
    """Crédito de efectivo no liquidado pendiente de settlement T+1/T+2."""

    amount: Decimal
    settlement_date: date


@dataclass
class SimulatedPosition:
    """Posición abierta en el broker simulado."""

    symbol: str
    qty: Decimal
    entry_price: Decimal
    entry_time: datetime
    initial_stop: Decimal
    current_stop: Decimal
    take_profit: Decimal | None
    strategy_id: str
    max_holding_bars: int = 0
    bars_held: int = 0
    exit_at_close: bool = False
    fees_paid: Decimal = Decimal("0.0")


@dataclass
class SimulatedTrade:
    """Registro de un trade cerrado y liquidado."""

    trade_id: str
    symbol: str
    strategy_id: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    pnl: Decimal
    pnl_pct: float
    pnl_r: float
    fees: Decimal
    slippage_cost: Decimal
    exit_reason: str
    initial_risk_usd: Decimal
    account_type: str = "cash"


class SimulatedBroker:
    """Simulador de broker para ejecución de órdenes en replay."""

    # Slippage por defecto en bps (puntos básicos: 1 bps = 0.0001)
    ETF_SYMBOLS = {"SPY", "QQQ", "IWM", "GLD", "SPYM", "QQQM", "GLDM", "TLT", "XLE", "XLV"}

    def __init__(
        self,
        initial_capital: Decimal = Decimal("2000.00"),
        etf_slippage_bps: float = 2.0,  # 2 bps para ETFs
        stock_slippage_bps: float = 5.0,  # 5 bps para acciones
        sec_fee_rate: Decimal = Decimal("0.0000278"),  # $27.80 por millón de USD nocional vendido
        finra_taf_per_share: Decimal = Decimal("0.000166"),  # $0.000166 por acción vendida
        max_finra_taf_per_order: Decimal = Decimal("8.30"),
        account_type: str = "cash",
        settlement_days: int = 1,
        etf_half_spread_bps: float = 0.0,
        stock_half_spread_bps: float = 0.0,
        cat_fee_per_share: Decimal = Decimal("0.00003"),
        integer_shares: bool = False,
    ) -> None:
        self.initial_capital = initial_capital
        self.account_type = account_type.lower()
        self.settlement_days = settlement_days
        self.settled_cash = initial_capital
        self.unsettled_cash = Decimal("0.0")
        self._unsettled_credits: list[UnsettledCredit] = []

        self.etf_slippage = Decimal(str(etf_slippage_bps / 10000.0))
        self.stock_slippage = Decimal(str(stock_slippage_bps / 10000.0))
        self.etf_half_spread = Decimal(str(etf_half_spread_bps / 10000.0))
        self.stock_half_spread = Decimal(str(stock_half_spread_bps / 10000.0))
        self.sec_fee_rate = sec_fee_rate
        self.finra_taf_per_share = finra_taf_per_share
        self.max_finra_taf = max_finra_taf_per_order
        self.cat_fee_per_share = cat_fee_per_share
        self.integer_shares = integer_shares

        self.positions: dict[str, SimulatedPosition] = {}
        self.closed_trades: list[SimulatedTrade] = []
        self.trade_counter = 0

    @property
    def cash(self) -> Decimal:
        """Efectivo total (liquidado + no liquidado)."""
        return self.settled_cash + self.unsettled_cash

    @cash.setter
    def cash(self, value: Decimal) -> None:
        """Permite asignar efectivo manteniendo compatibilidad hacia atrás."""
        diff = Decimal(str(value)) - (self.settled_cash + self.unsettled_cash)
        self.settled_cash += diff

    @property
    def buying_power(self) -> Decimal:
        """Poder de compra disponible según el tipo de cuenta.

        En cuenta Cash: solo fondos liquidados (settled cash) para prevenir Good Faith Violations (GFV).
        En cuenta Margin: efectivo total (settled + unsettled).
        """
        if self.account_type == "cash":
            return max(Decimal("0.0"), self.settled_cash)
        return max(Decimal("0.0"), self.cash)

    def calculate_settlement_date(self, trade_date: date | datetime) -> date:
        """Calcula la fecha de liquidación sumando días hábiles (omitiendo fines de semana)."""
        d = trade_date.date() if isinstance(trade_date, datetime) else trade_date
        added_days = 0
        while added_days < self.settlement_days:
            d += timedelta(days=1)
            if d.weekday() < 5:  # 0=Lunes, 4=Viernes
                added_days += 1
        return d

    def process_settlement(self, current_date: date | datetime) -> None:
        """Libera créditos no liquidados cuya fecha de liquidación haya llegado."""
        if not self._unsettled_credits:
            return
        cur_d = current_date.date() if isinstance(current_date, datetime) else current_date
        remaining: list[UnsettledCredit] = []
        for credit in self._unsettled_credits:
            if cur_d >= credit.settlement_date:
                self.settled_cash += credit.amount
                self.unsettled_cash = max(Decimal("0.0"), self.unsettled_cash - credit.amount)
            else:
                remaining.append(credit)
        self._unsettled_credits = remaining

    def _get_slippage_rate(self, symbol: str) -> Decimal:
        """Determina la tasa de slippage según si el símbolo es un ETF o una acción."""
        if symbol.upper() in self.ETF_SYMBOLS:
            return self.etf_slippage
        return self.stock_slippage

    def _get_half_spread_rate(self, symbol: str) -> Decimal:
        """Determina la tasa de medio spread según si el símbolo es un ETF o una acción."""
        if symbol.upper() in self.ETF_SYMBOLS:
            return self.etf_half_spread
        return self.stock_half_spread

    def calculate_sell_regulatory_fees(self, qty: Decimal, exit_price: Decimal) -> Decimal:
        """Calcula las tarifas regulatorias SEC fee, FINRA TAF y CAT fee en ventas."""
        notional = qty * exit_price
        sec_fee = notional * self.sec_fee_rate
        finra_fee = min(qty * self.finra_taf_per_share, self.max_finra_taf)
        cat_fee = qty * self.cat_fee_per_share
        return round(sec_fee + finra_fee + cat_fee, 4)

    def get_equity(self, current_prices: dict[str, Decimal]) -> Decimal:
        """Calcula el valor liquidativo total (efectivo + valor de mercado de posiciones)."""
        pos_value = Decimal("0.0")
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos.entry_price)
            pos_value += pos.qty * price
        return self.cash + pos_value

    def submit_buy(
        self,
        signal: Signal,
        qty: Decimal,
        next_bar_open: Decimal,
        timestamp: datetime,
    ) -> SimulatedPosition | None:
        """Ejecuta una orden de compra en la apertura de la barra siguiente con slippage y medio spread."""
        self.process_settlement(timestamp)
        available_funds = self.buying_power

        if self.integer_shares:
            qty = Decimal(int(qty))

        if qty <= 0 or available_funds <= 0:
            return None

        slippage_rate = self._get_slippage_rate(signal.symbol)
        half_spread_rate = self._get_half_spread_rate(signal.symbol)
        # Compra: Se paga ligeramente más debido al slippage y al medio spread
        fill_price = round(next_bar_open * (Decimal("1.0") + slippage_rate + half_spread_rate), 4)
        notional = qty * fill_price

        # Comprobar poder de compra disponible (previene GFV en cuentas Cash)
        if notional > available_funds:
            # Ajustar cantidad al poder de compra disponible
            qty = Decimal(int(available_funds / fill_price))
            if qty <= 0:
                return None
            notional = qty * fill_price

        self.settled_cash -= notional

        pos = SimulatedPosition(
            symbol=signal.symbol,
            qty=qty,
            entry_price=fill_price,
            entry_time=timestamp,
            initial_stop=signal.stop_price,
            current_stop=signal.stop_price,
            take_profit=signal.take_profit_price,
            strategy_id=signal.strategy_id,
            exit_at_close=signal.exit_at_close,
            max_holding_bars=(
                signal.max_holding
                if isinstance(signal.max_holding, int)
                else (int(signal.max_holding.total_seconds() // 3600) if isinstance(signal.max_holding, timedelta) else 0)
            ),
        )
        self.positions[signal.symbol] = pos
        return pos

    def close_position(
        self,
        symbol: str,
        exit_price_raw: Decimal,
        timestamp: datetime,
        reason: str,
        is_gap: bool = False,
    ) -> SimulatedTrade | None:
        """Cierra una posición abierta, deduciendo slippage, medio spread y tarifas regulatorias."""
        self.process_settlement(timestamp)
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        slippage_rate = self._get_slippage_rate(symbol)
        half_spread_rate = self._get_half_spread_rate(symbol)
        # Venta: Se recibe ligeramente menos debido al slippage y medio spread
        fill_price = round(exit_price_raw * (Decimal("1.0") - slippage_rate - half_spread_rate), 4)

        notional_gross = pos.qty * fill_price
        fees = self.calculate_sell_regulatory_fees(pos.qty, fill_price)
        net_proceeds = notional_gross - fees

        if self.account_type == "cash":
            self.unsettled_cash += net_proceeds
            settle_date = self.calculate_settlement_date(timestamp)
            self._unsettled_credits.append(
                UnsettledCredit(amount=net_proceeds, settlement_date=settle_date)
            )
        else:
            self.settled_cash += net_proceeds

        cost_basis = pos.qty * pos.entry_price
        pnl = net_proceeds - cost_basis
        pnl_pct = float(pnl / cost_basis) if cost_basis > 0 else 0.0

        # Cálculo de PnL en R (riesgo inicial asumido)
        initial_risk_per_unit = pos.entry_price - pos.initial_stop
        if initial_risk_per_unit > 0:
            initial_risk_total = pos.qty * initial_risk_per_unit
            pnl_r = float(pnl / initial_risk_total)
        else:
            initial_risk_total = Decimal("0.0")
            pnl_r = 0.0

        slippage_cost = pos.qty * (exit_price_raw - fill_price)

        self.trade_counter += 1
        trade = SimulatedTrade(
            trade_id=f"T{self.trade_counter:05d}",
            symbol=symbol,
            strategy_id=pos.strategy_id,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            qty=pos.qty,
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 6),
            pnl_r=round(pnl_r, 4),
            fees=fees,
            slippage_cost=round(slippage_cost, 4),
            exit_reason=reason,
            initial_risk_usd=round(initial_risk_total, 4),
            account_type=self.account_type,
        )
        self.closed_trades.append(trade)
        return trade

    def evaluate_bar(
        self,
        symbol: str,
        bar_open: Decimal,
        bar_high: Decimal,
        bar_low: Decimal,
        bar_close: Decimal,
        timestamp: datetime,
        is_market_close: bool = False,
    ) -> SimulatedTrade | None:
        """Evalúa si la barra activa el Stop Loss, Take Profit o salida forzada de cierre."""
        self.process_settlement(timestamp)
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        pos.bars_held += 1

        # 1. Salida obligatoria al cierre para estrategias intradía (exit_at_close)
        if pos.exit_at_close and is_market_close:
            return self.close_position(symbol, bar_close, timestamp, reason="exit_at_close")

        # 2. Evaluación de Stop Loss (con modelado de gaps)
        if bar_low <= pos.current_stop:
            # Si la barra abre con gap por debajo del stop, el fill es en open (peor caso)
            if bar_open <= pos.current_stop:
                exit_price = bar_open
                is_gap = True
            else:
                exit_price = pos.current_stop
                is_gap = False
            return self.close_position(
                symbol, exit_price, timestamp, reason="stop_loss", is_gap=is_gap
            )

        # 3. Evaluación de Take Profit (si está definido)
        if pos.take_profit is not None and bar_high >= pos.take_profit:
            # Si abre por encima del TP (gap favorable), el fill es en open
            exit_price = bar_open if bar_open >= pos.take_profit else pos.take_profit
            return self.close_position(symbol, exit_price, timestamp, reason="take_profit")

        return None
