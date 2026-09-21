"""Fachada principal del Veto de IA (AIVeto) con conmutación por error y falla cerrada."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog

from tbot.ai.providers import AIProvider, GeminiProvider, GroqProvider
from tbot.ai.quota import QuotaTracker
from tbot.ai.schemas import VetoInput, VetoOutput, VetoReasonCode, VetoResult, VetoVerdict
from tbot.news.models import NewsFeatures
from tbot.strategies.interfaces import Signal

logger = structlog.get_logger(__name__)


class AIVeto:
    """Orquestador de Veto de Riesgo y Noticias con modos cuantitativo y LLM."""

    def __init__(
        self,
        mode: Literal["off", "quantitative", "advisory", "required"] = "quantitative",
        primary_provider: AIProvider | None = None,
        fallback_provider: AIProvider | None = None,
        quota_tracker: QuotaTracker | None = None,
        prompt_version: str = "v1",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.mode = mode
        self.primary_provider = primary_provider or GeminiProvider()
        self.fallback_provider = fallback_provider or GroqProvider()
        self.quota_tracker = quota_tracker or QuotaTracker()
        self.prompt_version = prompt_version
        self.timeout_seconds = timeout_seconds

        self._prompt_text = self._load_prompt(prompt_version)

    def _load_prompt(self, version: str) -> str:
        """Carga el archivo de prompt versionado desde el sistema de archivos."""
        prompt_path = Path(__file__).parent / "prompts" / f"veto_{version}.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        # Prompt de reserva
        return (
            "Eres el Veto de IA de riesgo. Tu funcion es filtrar senales con problemas evidentes. "
            "Responde estrictamente en JSON con los campos: analysis, verdict, size_multiplier, reason_code."
        )

    def build_payload(
        self,
        signal: Signal,
        features: dict[str, Any],
        regime: str,
        events: dict[str, Any],
        news: NewsFeatures,
    ) -> VetoInput:
        """Construye y valida el payload de entrada sin texto libre de noticias."""
        signal_dict = {
            "symbol": signal.symbol,
            "strategy_id": signal.strategy_id,
            "entry_ref": float(signal.entry_price_ref),
            "stop": float(signal.stop_price),
            "take_profit": float(signal.take_profit_price) if signal.take_profit_price else None,
            "score": signal.score,
            "exit_at_close": signal.exit_at_close,
        }

        return VetoInput(
            signal=signal_dict,
            features=features,
            regime=regime,
            events=events,
            news=news.to_dict(),
        )

    async def review(
        self,
        signal: Signal,
        features: dict[str, Any],
        regime: str,
        events: dict[str, Any],
        news: NewsFeatures,
        now: datetime,
    ) -> VetoResult:
        """Evalúa la señal a través del Veto de IA siguiendo la política de falla cerrada."""
        if self.mode == "off":
            return VetoResult.bypassed()

        if self.mode == "quantitative":
            # Reglas matemáticas deterministas directas sobre NewsFeatures de FinBERT
            # 1. Pánico o cluster de noticias negativas
            if news.negative_share >= 0.35:
                return VetoResult(
                    verdict=VetoVerdict.REJECT,
                    size_multiplier=0.0,
                    reason_code=VetoReasonCode.NEWS_NEGATIVE_CLUSTER,
                    analysis=f"Rechazado por clúster de noticias negativas ({news.negative_share * 100:.1f}% rojas)",
                    status="OK",
                    provider="quantitative_engine",
                    latency_ms=0,
                )

            # 2. Litigios o investigaciones abiertas con sentimiento negativo
            if "litigation" in news.top_topics and news.sentiment_mean < 0.0:
                return VetoResult(
                    verdict=VetoVerdict.REJECT,
                    size_multiplier=0.0,
                    reason_code=VetoReasonCode.EVENT_RISK,
                    analysis=f"Rechazado por riesgo legal/investigación regulatoria (score {news.sentiment_mean})",
                    status="OK",
                    provider="quantitative_engine",
                    latency_ms=0,
                )

            # 3. Recorte de guidance con sentimiento débil
            if "guidance" in news.top_topics and news.sentiment_mean < -0.15:
                return VetoResult(
                    verdict=VetoVerdict.REDUCE,
                    size_multiplier=0.5,
                    reason_code=VetoReasonCode.NEWS_NEGATIVE_CLUSTER,
                    analysis="Tamaño reducido al 50% por recorte de proyecciones (guidance adverso)",
                    status="OK",
                    provider="quantitative_engine",
                    latency_ms=0,
                )

            return VetoResult(
                verdict=VetoVerdict.CONFIRM,
                size_multiplier=1.0,
                reason_code=VetoReasonCode.OK,
                analysis="Aprobado: métricas cuantitativas de noticias dentro de parámetros normales",
                status="OK",
                provider="quantitative_engine",
                latency_ms=0,
            )

        # Construir y validar payload de entrada para modos LLM (advisory / required)
        try:
            veto_input = self.build_payload(signal, features, regime, events, news)
            payload_json = veto_input.model_dump_json(indent=2)
        except Exception as e:
            logger.error("Error al construir VetoInput", error=str(e), symbol=signal.symbol)
            return VetoResult.unavailable(f"Error de serialización de entrada: {e}")

        # Intentar proveedor primario si tiene cuota disponible
        providers_to_try: list[AIProvider] = []
        if self.quota_tracker.can_request(self.primary_provider.provider_name, now):
            providers_to_try.append(self.primary_provider)
        else:
            logger.warning(
                "Cuota primaria agotada o excedida del 90%",
                provider=self.primary_provider.provider_name,
            )

        # Respaldo
        if self.quota_tracker.can_request(self.fallback_provider.provider_name, now):
            providers_to_try.append(self.fallback_provider)
        else:
            logger.warning(
                "Cuota de respaldo agotada o excedida del 90%",
                provider=self.fallback_provider.provider_name,
            )

        if not providers_to_try:
            logger.error("Todos los proveedores de LLM han excedido sus cuotas de seguridad")
            return VetoResult.unavailable("Cuota de LLM agotada en todos los proveedores")

        last_error = ""

        for provider in providers_to_try:
            try:
                raw_text, latency_ms, tokens_in, tokens_out = await provider.query(
                    system_prompt=self._prompt_text,
                    payload_json=payload_json,
                    timeout_seconds=self.timeout_seconds,
                )

                # Validar salida estructurada estricta
                # Limpiar posibles bloques ```json ... ``` accidentales
                clean_json = raw_text.strip()
                if clean_json.startswith("```"):
                    lines = clean_json.splitlines()
                    clean_json = "\n".join(lines[1:-1])

                output = VetoOutput.model_validate_json(clean_json)

                # Registrar consumo de cuota
                self.quota_tracker.record_request(provider.provider_name, now)

                # Si el modo es advisory, no bloquea aunque sugiera rechazo
                final_verdict = output.verdict
                final_multiplier = output.size_multiplier
                if self.mode == "advisory":
                    # En modo advisory registramos el consejo pero confirmamos la ejecución
                    final_verdict = VetoVerdict.CONFIRM
                    final_multiplier = 1.0

                return VetoResult(
                    verdict=final_verdict,
                    size_multiplier=final_multiplier,
                    reason_code=output.reason_code,
                    analysis=output.analysis,
                    status="OK",
                    provider=provider.provider_name,
                    model_name=provider.model_name,
                    latency_ms=latency_ms,
                )
            except Exception as e:
                last_error = f"{provider.provider_name}: {e}"
                logger.warning(
                    "Fallo en llamada a proveedor de IA, intentando siguiente...",
                    provider=provider.provider_name,
                    error=str(e),
                )

        # Si todos los intentos fallan -> Falla cerrada
        logger.error("Todos los proveedores fallaron", last_error=last_error)
        return VetoResult.unavailable(f"Falla de conexión/formato con LLM ({last_error})")
