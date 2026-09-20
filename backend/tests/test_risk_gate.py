"""Tests unitarios para la compuerta central RiskGate."""

from datetime import datetime
from decimal import Decimal

from tbot.risk.gate import RiskGate
from tbot.risk.models import RiskReasonCode
from tbot.risk.ownership import OwnershipLedger
from tbot.strategies.interfaces import Signal


def _make_signal(symbol: str = "AAPL", entry: str = "100.00", stop: str = "95.00") -> Signal:
    return Signal(
        signal_id="sig_test_1",
        strategy_id="s3_trend_pullback",
        symbol=symbol,
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal(entry),
        stop_price=Decimal(stop),
        created_at=datetime(2026, 9, 21, 15, 45),
        take_profit_price=Decimal("110.00"),
        score=0.85,
        exit_at_close=False,
    )


def test_risk_gate_rejects_on_kill_switch() -> None:
    gate = RiskGate()
    sig = _make_signal()

    decision = gate.evaluate(
        signal=sig,
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        open_positions={},
        kill_switch_active=True,
    )

    assert decision.approved is False
    assert decision.reason_code == RiskReasonCode.REJECT_KILL_SWITCH


def test_risk_gate_rejects_on_max_open_positions() -> None:
    gate = RiskGate(max_open_positions=2)
    sig = _make_signal("MSFT")

    open_pos = {"AAPL": object(), "NVDA": object()}
    decision = gate.evaluate(
        signal=sig,
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        open_positions=open_pos,
    )

    assert decision.approved is False
    assert decision.reason_code == RiskReasonCode.REJECT_MAX_POSITIONS


def test_risk_gate_rejects_duplicate_symbol_in_positions() -> None:
    gate = RiskGate()
    sig = _make_signal("AAPL")

    open_pos = {"AAPL": object()}
    decision = gate.evaluate(
        signal=sig,
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        open_positions=open_pos,
    )

    assert decision.approved is False
    assert decision.reason_code == RiskReasonCode.REJECT_MAX_POSITIONS


def test_risk_gate_rejects_on_ownership_conflict() -> None:
    ledger = OwnershipLedger()
    ledger.assign("AAPL", "manual")  # El usuario opera AAPL manualmente

    gate = RiskGate(ownership_ledger=ledger)
    sig = _make_signal("AAPL")

    decision = gate.evaluate(
        signal=sig,
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        open_positions={},
    )

    assert decision.approved is False
    assert decision.reason_code == RiskReasonCode.REJECT_OWNERSHIP_CONFLICT


def test_risk_gate_rejects_on_cluster_limit() -> None:
    gate = RiskGate(max_positions_per_cluster=2)
    sig = _make_signal("NVDA")

    # Ambas posiciones abiertas pertenecen al cluster 'tech'
    open_pos = {"AAPL": object(), "MSFT": object()}
    clusters = {"AAPL": "tech", "MSFT": "tech", "NVDA": "tech"}

    decision = gate.evaluate(
        signal=sig,
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        open_positions=open_pos,
        clusters_map=clusters,
    )

    assert decision.approved is False
    assert decision.reason_code == RiskReasonCode.REJECT_CLUSTER_LIMIT


def test_risk_gate_approves_valid_signal() -> None:
    gate = RiskGate()
    sig = _make_signal("AAPL", entry="100.00", stop="95.00")

    decision = gate.evaluate(
        signal=sig,
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        open_positions={},
    )

    assert decision.approved is True
    assert decision.reason_code == RiskReasonCode.APPROVED
    assert decision.sizing is not None
    assert decision.sizing.shares >= Decimal("1")
