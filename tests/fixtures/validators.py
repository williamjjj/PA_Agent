"""Shared JsonValidator instances for unit/property tests."""
from __future__ import annotations

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.config.settings import ValidationSettings


def schema_test_validator() -> JsonValidator:
    """Lenient normalization + no trace semantics (schema/coherence focus)."""
    return JsonValidator(
        ValidationSettings(
            normalization_mode="lenient",
            trace_semantic_checks=False,
            strict_bar_by_bar_features=False,
            disable_truncation_repair=False,
        )
    )


def strict_test_validator() -> JsonValidator:
    """Explicit strict normalization/syntax; independent of user-facing defaults."""
    return JsonValidator(ValidationSettings(
        normalization_mode="strict", disable_truncation_repair=True,
    ))
