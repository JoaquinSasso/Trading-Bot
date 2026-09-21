"""Extracción determinista de tópicos financieros a partir de titulares."""

from __future__ import annotations

import re

TOPIC_KEYWORDS: dict[str, set[str]] = {
    "earnings": {
        "earnings",
        "eps",
        "revenue",
        "quarter",
        "q1",
        "q2",
        "q3",
        "q4",
        "fiscal",
        "results",
        "profit",
    },
    "guidance": {
        "guidance",
        "forecast",
        "outlook",
        "projects",
        "estimates",
        "cuts outlook",
        "raises guidance",
    },
    "litigation": {
        "lawsuit",
        "sued",
        "suing",
        "court",
        "probe",
        "investigation",
        "sec",
        "doj",
        "fraud",
        "subpoena",
        "settlement",
    },
    "m&a": {"merger", "acquisition", "acquires", "buyout", "takeover", "deal", "spinoff", "merges"},
    "fda_reg": {
        "fda",
        "approval",
        "clinical",
        "trial",
        "phase",
        "patent",
        "regulator",
        "antitrust",
        "ftc",
    },
    "macro": {
        "inflation",
        "fed",
        "powell",
        "rate",
        "rates",
        "cpi",
        "gdp",
        "tariff",
        "tariffs",
        "jobs",
        "recession",
    },
}


class TopicExtractor:
    """Extrae etiquetas temáticas estándar a partir de reglas léxicas deterministas."""

    @staticmethod
    def extract_topics(text: str) -> list[str]:
        """Identifica los tópicos aplicables en el texto."""
        if not text:
            return []

        text_lower = text.lower()
        matched_topics: list[str] = []

        for topic, keywords in TOPIC_KEYWORDS.items():
            for kw in keywords:
                # Búsqueda exacta de palabra o frase corta
                pattern = rf"\b{re.escape(kw)}\b"
                if re.search(pattern, text_lower):
                    matched_topics.append(topic)
                    break

        return matched_topics
