"""Proveedores de inferencia LLM para el Veto de IA (Gemini, Groq y Mock)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol, runtime_checkable

from tbot.config.settings import settings


@runtime_checkable
class AIProvider(Protocol):
    """Protocolo formal para clientes de modelos de lenguaje."""

    provider_name: str
    model_name: str

    async def query(
        self,
        system_prompt: str,
        payload_json: str,
        timeout_seconds: float = 20.0,
    ) -> tuple[str, int, int | None, int | None]:
        """Envía el payload al modelo y retorna (response_raw, latency_ms, tokens_in, tokens_out)."""
        ...


class GeminiProvider:
    """Proveedor LLM primario utilizando google-genai (Gemini Flash)."""

    provider_name: str = "gemini"

    def __init__(self, model_name: str = "gemini-2.5-flash", api_key: str | None = None) -> None:
        self.model_name = model_name
        self.api_key = api_key or settings.GEMINI_API_KEY
        self._client = None

        if self.api_key:
            try:
                from google import genai

                self._client = genai.Client(api_key=self.api_key)
            except Exception:
                self._client = None

    async def query(
        self,
        system_prompt: str,
        payload_json: str,
        timeout_seconds: float = 20.0,
    ) -> tuple[str, int, int | None, int | None]:
        if not self._client:
            raise RuntimeError("Gemini client no inicializado o GEMINI_API_KEY faltante.")

        t_start = time.perf_counter()

        def _call_gemini() -> Any:
            from google.genai import types

            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                response_mime_type="application/json",
            )
            return self._client.models.generate_content(
                model=self.model_name,
                contents=payload_json,
                config=config,
            )

        # Ejecutar con timeout estricto de 20s en hilo asíncrono
        resp = await asyncio.wait_for(asyncio.to_thread(_call_gemini), timeout=timeout_seconds)
        latency_ms = int((time.perf_counter() - t_start) * 1000)

        response_text = resp.text if hasattr(resp, "text") else str(resp)
        tokens_in = (
            resp.usage_metadata.prompt_token_count
            if hasattr(resp, "usage_metadata") and resp.usage_metadata
            else None
        )
        tokens_out = (
            resp.usage_metadata.candidates_token_count
            if hasattr(resp, "usage_metadata") and resp.usage_metadata
            else None
        )

        return response_text, latency_ms, tokens_in, tokens_out


class GroqProvider:
    """Proveedor LLM de respaldo utilizando Groq (Llama 3.3 70B)."""

    provider_name: str = "groq"

    def __init__(
        self, model_name: str = "llama-3.3-70b-versatile", api_key: str | None = None
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key or settings.GROQ_API_KEY
        self._client = None

        if self.api_key:
            try:
                from groq import AsyncGroq

                self._client = AsyncGroq(api_key=self.api_key)
            except Exception:
                self._client = None

    async def query(
        self,
        system_prompt: str,
        payload_json: str,
        timeout_seconds: float = 20.0,
    ) -> tuple[str, int, int | None, int | None]:
        if not self._client:
            raise RuntimeError("Groq client no inicializado o GROQ_API_KEY faltante.")

        t_start = time.perf_counter()

        resp = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload_json},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            ),
            timeout=timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - t_start) * 1000)

        content = resp.choices[0].message.content or "{}"
        tokens_in = resp.usage.prompt_tokens if hasattr(resp, "usage") and resp.usage else None
        tokens_out = resp.usage.completion_tokens if hasattr(resp, "usage") and resp.usage else None

        return content, latency_ms, tokens_in, tokens_out


class MockAIProvider:
    """Proveedor simulado para tests unitarios y validación offline determinista."""

    provider_name: str = "mock"
    model_name: str = "mock-v1"

    def __init__(
        self,
        default_verdict: str = "CONFIRM",
        size_multiplier: float = 1.0,
        reason_code: str = "OK",
        analysis: str = "Setup técnico limpio con indicadores alineados y noticias neutras.",
        simulate_timeout: bool = False,
        simulate_error: bool = False,
        simulate_invalid_json: bool = False,
        latency_ms: int = 50,
    ) -> None:
        self.default_verdict = default_verdict
        self.size_multiplier = size_multiplier
        self.reason_code = reason_code
        self.analysis = analysis
        self.simulate_timeout = simulate_timeout
        self.simulate_error = simulate_error
        self.simulate_invalid_json = simulate_invalid_json
        self.latency_ms = latency_ms

    async def query(
        self,
        system_prompt: str,
        payload_json: str,
        timeout_seconds: float = 20.0,
    ) -> tuple[str, int, int | None, int | None]:
        if self.simulate_timeout:
            await asyncio.sleep(timeout_seconds + 0.5)

        if self.simulate_error:
            raise ConnectionError("Fallo simulado de conexión con la API de IA.")

        if self.simulate_invalid_json:
            return "{invalid_json_content", self.latency_ms, 100, 20

        # Respuesta estructurada válida conforme al esquema estricto
        response_dict = {
            "analysis": self.analysis[:400],
            "verdict": self.default_verdict,
            "size_multiplier": self.size_multiplier,
            "reason_code": self.reason_code,
        }
        return json.dumps(response_dict), self.latency_ms, 120, 35
