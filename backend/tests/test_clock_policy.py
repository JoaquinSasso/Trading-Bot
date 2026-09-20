"""Test de cumplimiento del Principio No Negociable 6: Reloj Inyectable.

Verifica estáticamente que ningún archivo dentro del paquete `tbot/` invoque
`datetime.now()` o `datetime.utcnow()` directamente, con la única excepción
autorizada de `tbot/common/clock.py` (dentro de SystemClock).
"""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tbot.common.clock import Clock, SimulatedClock, SystemClock


class DatetimeCallVisitor(ast.NodeVisitor):
    """Recorre el AST buscando llamadas a .now() o .utcnow()."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Detectar datetime.now() o datetime.utcnow()
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("now", "utcnow"):
            # Comprobar si el llamador es datetime o similar
            caller_name = ""
            if isinstance(node.func.value, ast.Name):
                caller_name = node.func.value.id
            elif isinstance(node.func.value, ast.Attribute):
                caller_name = node.func.value.attr

            if caller_name in ("datetime", "dt"):
                self.violations.append((node.lineno, f"{caller_name}.{node.func.attr}()"))

        self.generic_visit(node)


def test_no_direct_datetime_now_in_codebase() -> None:
    """Escanea todo el código en tbot/ para garantizar que nadie llame a datetime.now() directo."""
    tbot_dir = Path(__file__).resolve().parent.parent / "tbot"
    assert tbot_dir.exists(), f"Directorio no encontrado: {tbot_dir}"

    all_violations: list[str] = []

    for py_file in tbot_dir.rglob("*.py"):
        # La única excepción permitida por diseño es SystemClock en clock.py
        if py_file.name == "clock.py" and py_file.parent.name == "common":
            continue

        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        visitor = DatetimeCallVisitor(str(py_file))
        visitor.visit(tree)

        for lineno, call_str in visitor.violations:
            rel_path = py_file.relative_to(tbot_dir)
            all_violations.append(f"{rel_path}:{lineno} -> {call_str}")

    assert not all_violations, (
        "Se encontraron llamadas directas no autorizadas a datetime.now()/utcnow(). "
        "Todo módulo debe recibir una instancia de Clock inyectada:\n"
        + "\n".join(all_violations)
    )


def test_system_clock_returns_utc_aware_datetime() -> None:
    """Verifica que SystemClock retorne datetimes con timezone UTC."""
    clock: Clock = SystemClock()
    current = clock.now()
    assert isinstance(current, datetime)
    assert current.tzinfo is not None
    assert current.tzinfo == UTC
    assert clock.today() == current.date()


def test_simulated_clock_manual_control() -> None:
    """Verifica que SimulatedClock avance y se congele determinísticamente."""
    start = datetime(2026, 9, 19, 9, 30, 0, tzinfo=UTC)
    clock = SimulatedClock(initial_time=start)

    assert clock.now() == start
    assert clock.today() == start.date()

    # Avanzar 5 minutos
    clock.advance(timedelta(minutes=5))
    expected_new = datetime(2026, 9, 19, 9, 35, 0, tzinfo=UTC)
    assert clock.now() == expected_new

    # Establecer hora explícita
    target = datetime(2026, 9, 20, 16, 0, 0, tzinfo=UTC)
    clock.set_time(target)
    assert clock.now() == target
    assert clock.today() == target.date()
