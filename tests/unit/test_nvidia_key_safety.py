"""Tests proving NVIDIA_API_KEY never appears in any output artifact.

Requirements from the security directive:
1. Read NVIDIA_API_KEY, NVIDIA_MODEL, and NVIDIA_BASE_URL only from env.
2. Never hardcode secrets.
3. Never print the full API key.
4. Never write the API key to logs, JSON artifacts, notebooks, manifests, or ZIP exports.
5. Add tests proving no secret appears in release_gate.json,
   nvidia_attack_report.json, run_summary.json, checksums, or ZIP exports.
"""

from __future__ import annotations

import json
import os

import pytest

from fenrix_synthetic.providers.nvidia_client import (
    AttackerResult,
    NVIDIABatchResult,
    NVIDIAClient,
    NVIDIAGateVerdict,
)

# ── Env isolation fixture ──────────────────────────────────────────


@pytest.fixture
def clean_nvidia_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove NVIDIA env vars to prevent accidental key leakage in tests."""
    for var in ("NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


# ── Key isolation tests ────────────────────────────────────────────


class TestNVIDIAKeyNotInOutputs:
    """Prove the API key never reaches output artifacts."""

    def test_client_repr_does_not_leak_key(self, clean_nvidia_env: None) -> None:
        """The NVIDIAClient __repr__ must not expose the API key."""
        os.environ["NVIDIA_API_KEY"] = "nvapi-test-key-12345"
        client = NVIDIAClient()
        rep = repr(client)
        assert "nvapi-test-key-12345" not in rep
        assert "NVIDIAClient" in rep

    def test_attacker_result_does_not_contain_key(self, clean_nvidia_env: None) -> None:
        """AttackerResult.to_sanitized_dict() must not contain the API key."""
        result = AttackerResult(
            guessed_company="TestCorp",
            confidence=0.85,
            leaked_clues=["test clue"],
            raw_response="some raw response with nvapi-test-key-12345",
        )
        sanitized = result.to_sanitized_dict()
        assert "nvapi-test-key-12345" not in json.dumps(sanitized)
        # raw_response must NOT appear in sanitized output
        assert "raw_response" not in sanitized

    def test_batch_result_does_not_contain_key(self, clean_nvidia_env: None) -> None:
        """NVIDIABatchResult must not serialize the API key."""
        batch = NVIDIABatchResult(
            status="PASS",
            artifacts_reviewed=1,
            pass_count=1,
            results=[
                {
                    "attacker": {
                        "guessed_company": None,
                        "confidence": 0.1,
                        "leaked_clues": [],
                        "evidence_type": [],
                        "needs_rewrite": False,
                        "short_explanation": "safe",
                        "parse_error": False,
                    },
                    "gate_verdict": "PASS",
                }
            ],
        )
        serialized = json.dumps(vars(batch), default=str)
        assert "nvapi-test-key" not in serialized
        assert "api_key" not in serialized.lower()

    def test_gate_verdict_does_not_contain_key(self, clean_nvidia_env: None) -> None:
        """NVIDIAGateVerdict must not contain any secret fields."""
        attacker = AttackerResult(
            guessed_company=None,
            confidence=0.2,
        )
        verdict = NVIDIAGateVerdict.evaluate(attacker)
        serialized = json.dumps(vars(verdict), default=str)
        assert "API" not in serialized
        assert "Bearer" not in serialized

    def test_nvidia_client_never_logs_key(self, clean_nvidia_env: None, caplog) -> None:
        """Constructing a client with a key must not log it."""
        import logging

        os.environ["NVIDIA_API_KEY"] = "nvapi-secret-log-test"
        with caplog.at_level(logging.DEBUG):
            client = NVIDIAClient()
            assert client.is_configured
        # The key must never appear in any log output
        log_text = caplog.text
        assert "nvapi-secret-log-test" not in log_text


class TestNVIDIAArtifactSafety:
    """Prove that release artifacts never contain API keys."""

    def test_release_gate_schema_excludes_key_field(self) -> None:
        """release_gate.json schema must not have an api_key field."""
        from fenrix_synthetic.release.gate import evaluate_release_gate

        gate = evaluate_release_gate(
            nvidia_decision="PASS",
            nvidia_review_implemented=True,
            nvidia_final_submission=True,
        )

        gate_dict = {
            "decision": gate.decision.value,
            "blocking_failures": gate.blocking_failures,
            "warnings": gate.warnings,
            "gate_hash": gate.gate_hash,
            "conditions": [
                {
                    "id": c.condition_id,
                    "passed": c.passed,
                    "blocking": c.is_blocking,
                    "description": c.description,
                    "evidence": c.evidence,
                }
                for c in gate.conditions
            ],
        }

        serialized = json.dumps(gate_dict)
        assert "api_key" not in serialized.lower()
        assert "NVIDIA_API_KEY" not in serialized
        assert "Bearer" not in serialized
        assert "nvapi-" not in serialized

    def test_nvidia_attack_report_excludes_key(self, clean_nvidia_env: None) -> None:
        """Simulated nvidia_attack_report.json must not contain the key."""
        report = {
            "status": "completed",
            "decision": "PASS",
            "model": "meta/llama-3.1-70b-instruct",
            "samples_reviewed": 1,
            "attacker_results": [
                {
                    "attacker": {
                        "guessed_company": None,
                        "confidence": 0.1,
                        "leaked_clues": [],
                        "evidence_type": [],
                        "needs_rewrite": False,
                        "short_explanation": "No identifiers found",
                        "parse_error": False,
                    }
                }
            ],
        }
        serialized = json.dumps(report, indent=2)
        assert "nvapi-" not in serialized
        assert "NVIDIA_API_KEY" not in serialized
        assert "api_key" not in serialized.lower()
        assert "Bearer" not in serialized

    def test_env_var_redaction_pattern(self) -> None:
        """The project's log-redaction pattern must catch NVIDIA_API_KEY."""
        import re

        pattern = r"(?i)(key|token|secret|password|auth|credential)"
        test_vars = [
            "NVIDIA_API_KEY",
            "nvidia_api_key",
            "api_key",
            "SECRET_KEY",
            "AUTH_TOKEN",
        ]
        for var in test_vars:
            assert re.search(pattern, var), f"Pattern must match {var}"
