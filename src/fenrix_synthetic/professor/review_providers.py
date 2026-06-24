"""Review provider protocol and implementations for adversarial QA.

Defines the review provider protocol that connects the ADVERSARIAL_QA
stage to model-based review for identity leakage detection.

Three implementations:
- MockReviewProvider: deterministic PASS for fixture/local-dev
- NVIDIAReviewProvider: real NVIDIA-hosted review for production
- OfflineFixtureReviewProvider: recorded response replay for tests
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ReviewFinding(BaseModel):
    """A single finding from adversarial review."""

    finding_id: str
    finding_type: str  # direct_identifier, semantic_clue, numeric_fingerprint, rare_phrase, etc.
    severity: str  # blocking, warning, info
    artifact_id: str = ""
    evidence_span: str = ""  # the text span that triggered the finding
    description: str = ""
    confidence: float = 0.0

    model_config = {"extra": "forbid"}


class ReviewArtifact(BaseModel):
    """An artifact to be reviewed for identity leakage."""

    artifact_id: str
    artifact_type: str  # sec_section, metric_series, news_surrogate, etc.
    content: str
    company_id: str = ""
    provenance_key: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ReviewPolicy(BaseModel):
    """Policy governing adversarial review."""

    policy_id: str
    policy_version: str
    require_direct_identifier_scan: bool = True
    require_source_company_guess: bool = True
    require_evidence_backed_guess: bool = True
    require_residual_ticker_scan: bool = True
    require_residual_cik_scan: bool = True
    require_residual_accession_scan: bool = True
    require_residual_url_scan: bool = True
    require_rare_phrase_scan: bool = True
    require_numeric_fingerprint_scan: bool = True
    require_news_headline_scan: bool = True
    require_cross_artifact_consistency_scan: bool = True
    require_reverse_engineer_judgment: bool = True
    min_artifact_level_risk_score: float = 0.0
    max_bundle_level_risk_score: float = 0.50
    block_on_direct_identifier: bool = True
    block_on_likely_source_identity: bool = True

    model_config = {"extra": "forbid"}


class ReviewReport(BaseModel):
    """Report from adversarial review of a set of artifacts."""

    report_id: str
    provider_name: str
    provider_kind: str
    model_id: str = ""
    model_version: str = ""
    policy_id: str = ""
    policy_version: str = ""
    artifacts_reviewed: int = 0
    artifact_level_risk_scores: dict[str, float] = Field(default_factory=dict)
    bundle_level_risk_score: float = 0.0
    guessed_source_identities: list[str] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    empty_evidence_count: int = 0
    malformed_response_count: int = 0
    direct_identifier_findings: list[ReviewFinding] = Field(default_factory=list)
    semantic_clue_findings: list[ReviewFinding] = Field(default_factory=list)
    numeric_fingerprint_findings: list[ReviewFinding] = Field(default_factory=list)
    release_recommendation: str = ""  # release, review_required, block
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    succeeded: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    model_config = {"extra": "forbid"}


class ReviewProvider(Protocol):
    """Protocol for adversarial QA providers."""

    def review_artifacts(
        self,
        artifacts: Sequence[ReviewArtifact],
        *,
        policy: ReviewPolicy,
        run_id: str,
    ) -> ReviewReport:
        """Review artifacts for identity leakage. Returns a structured report."""
        ...

    def health_check(self) -> bool:
        """Return True if the provider is operational."""
        ...

    @property
    def provider_name(self) -> str: ...

    @property
    def provider_kind(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def model_version(self) -> str: ...


# ── Default review policy ──────────────────────────────────────────────────


def default_review_policy() -> ReviewPolicy:
    """Return the default adversarial review policy."""
    return ReviewPolicy(
        policy_id="adversarial_review_v1",
        policy_version="1.0.0",
        require_direct_identifier_scan=True,
        require_source_company_guess=True,
        require_evidence_backed_guess=True,
        require_residual_ticker_scan=True,
        require_residual_cik_scan=True,
        require_residual_accession_scan=True,
        require_residual_url_scan=True,
        require_rare_phrase_scan=True,
        require_numeric_fingerprint_scan=True,
        require_news_headline_scan=True,
        require_cross_artifact_consistency_scan=True,
        require_reverse_engineer_judgment=True,
        min_artifact_level_risk_score=0.0,
        max_bundle_level_risk_score=0.50,
        block_on_direct_identifier=True,
        block_on_likely_source_identity=True,
    )


# ── MockReviewProvider (fixture/local-dev) ─────────────────────────────────


class MockReviewProvider:
    """Deterministic mock review provider for fixture and local-dev modes.

    Always returns a PASS with low confidence, no findings, and a release
    recommendation. This is NOT acceptable for production builds.
    """

    PROVIDER_NAME = "mock_review"
    PROVIDER_KIND = "mock"
    MODEL_ID = "mock-review-model-v1"
    MODEL_VERSION = "1.0.0-mock"

    def review_artifacts(
        self,
        artifacts: Sequence[ReviewArtifact],
        *,
        policy: ReviewPolicy,
        run_id: str,
    ) -> ReviewReport:
        finding_id_base = hashlib.sha256(run_id.encode()).hexdigest()[:8]
        return ReviewReport(
            report_id=f"review-{finding_id_base}",
            provider_name=self.PROVIDER_NAME,
            provider_kind=self.PROVIDER_KIND,
            model_id=self.MODEL_ID,
            model_version=self.MODEL_VERSION,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            artifacts_reviewed=len(artifacts),
            artifact_level_risk_scores={a.artifact_id: 0.05 for a in artifacts},
            bundle_level_risk_score=0.05,
            guessed_source_identities=[],
            findings=[],
            empty_evidence_count=0,
            malformed_response_count=0,
            direct_identifier_findings=[],
            semantic_clue_findings=[],
            numeric_fingerprint_findings=[],
            release_recommendation="release",
            blockers=[],
            warnings=[],
            succeeded=True,
        )

    def health_check(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def provider_kind(self) -> str:
        return self.PROVIDER_KIND

    @property
    def model_id(self) -> str:
        return self.MODEL_ID

    @property
    def model_version(self) -> str:
        return self.MODEL_VERSION


# ── NVIDIAReviewProvider (production) ─────────────────────────────────────


class NVIDIAReviewProvider:
    """NVIDIA-hosted review provider for production adversarial QA.

    Requires NVIDIA_API_KEY environment variable. Missing key causes
    health_check() to return False and review_artifacts() to raise.
    """

    PROVIDER_NAME = "nvidia_review"
    PROVIDER_KIND = "real"
    MODEL_ID = "meta/llama-3.1-70b-instruct"
    MODEL_VERSION = "1.0.0-nvidia"

    def __init__(self, api_key: str | None = None) -> None:
        import os

        self._api_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        self._missing_key = not self._api_key
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._missing_key:
            raise RuntimeError(
                "NVIDIA_API_KEY not configured. "
                "Set NVIDIA_API_KEY environment variable or pass api_key to constructor."
            )
        if self._client is not None:
            return self._client
        from openai import OpenAI

        self._client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=self._api_key,
        )
        return self._client

    def review_artifacts(
        self,
        artifacts: Sequence[ReviewArtifact],
        *,
        policy: ReviewPolicy,
        run_id: str,
    ) -> ReviewReport:
        client = self._ensure_client()
        artifact_descriptions = "\n".join(
            f"--- Artifact {a.artifact_id} ({a.artifact_type}) ---\n{a.content[:2000]}"
            for a in artifacts
        )
        prompt = (
            "You are an adversarial privacy reviewer. Review the following artifacts "
            "for any identity leakage that could reveal the true source company.\n\n"
            f"Policy: {policy.model_dump_json()}\n\n"
            f"Artifacts:\n{artifact_descriptions}\n\n"
            "Return a JSON object with:\n"
            "- direct_identifier_findings: list of {finding_type, severity, evidence_span, description, confidence}\n"
            "- semantic_clue_findings: list of {finding_type, severity, evidence_span, description, confidence}\n"
            "- numeric_fingerprint_findings: list of {finding_type, severity, description, confidence}\n"
            "- guessed_source_identities: list of strings\n"
            "- bundle_level_risk_score: float 0-1\n"
            "- release_recommendation: 'release', 'review_required', or 'block'\n"
            "- blockers: list of strings\n"
            "- warnings: list of strings\n"
        )
        try:
            response = client.chat.completions.create(
                model=self.MODEL_ID,
                messages=[
                    {"role": "system", "content": "You are an adversarial privacy reviewer."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            content = response.choices[0].message.content or ""
            parsed = json.loads(content)
        except Exception as e:
            return ReviewReport(
                report_id=f"review-{hashlib.sha256(run_id.encode()).hexdigest()[:8]}",
                provider_name=self.PROVIDER_NAME,
                provider_kind=self.PROVIDER_KIND,
                model_id=self.MODEL_ID,
                model_version=self.MODEL_VERSION,
                succeeded=False,
                release_recommendation="block",
                blockers=[f"NVIDIA review failed: {e}"],
            )

        findings_list: list[ReviewFinding] = []
        di_findings: list[ReviewFinding] = []
        for raw in parsed.get("direct_identifier_findings", []):
            f = ReviewFinding(
                finding_id=f"di-{hashlib.sha256(str(raw).encode()).hexdigest()[:8]}",
                finding_type=raw.get("finding_type", "direct_identifier"),
                severity=raw.get("severity", "blocking"),
                evidence_span=raw.get("evidence_span", ""),
                description=raw.get("description", ""),
                confidence=raw.get("confidence", 0.0),
            )
            findings_list.append(f)
            di_findings.append(f)

        sc_findings: list[ReviewFinding] = []
        for raw in parsed.get("semantic_clue_findings", []):
            f = ReviewFinding(
                finding_id=f"sc-{hashlib.sha256(str(raw).encode()).hexdigest()[:8]}",
                finding_type=raw.get("finding_type", "semantic_clue"),
                severity=raw.get("severity", "warning"),
                evidence_span=raw.get("evidence_span", ""),
                description=raw.get("description", ""),
                confidence=raw.get("confidence", 0.0),
            )
            findings_list.append(f)
            sc_findings.append(f)

        nf_findings: list[ReviewFinding] = []
        for raw in parsed.get("numeric_fingerprint_findings", []):
            f = ReviewFinding(
                finding_id=f"nf-{hashlib.sha256(str(raw).encode()).hexdigest()[:8]}",
                finding_type=raw.get("finding_type", "numeric_fingerprint"),
                severity=raw.get("severity", "warning"),
                description=raw.get("description", ""),
                confidence=raw.get("confidence", 0.0),
            )
            findings_list.append(f)
            nf_findings.append(f)

        guessed = parsed.get("guessed_source_identities", [])
        bundle_risk = float(parsed.get("bundle_level_risk_score", 0.0))
        recommendation = parsed.get("release_recommendation", "review_required")
        blockers = parsed.get("blockers", [])
        warnings = parsed.get("warnings", [])

        # Enforce policy rules
        if policy.block_on_direct_identifier and di_findings:
            blockers.append("Direct identifier findings block release")
        if policy.block_on_likely_source_identity and guessed:
            blockers.append("Likely source identity guess blocks release")

        return ReviewReport(
            report_id=f"review-{hashlib.sha256(run_id.encode()).hexdigest()[:8]}",
            provider_name=self.PROVIDER_NAME,
            provider_kind=self.PROVIDER_KIND,
            model_id=self.MODEL_ID,
            model_version=self.MODEL_VERSION,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            artifacts_reviewed=len(artifacts),
            artifact_level_risk_scores={a.artifact_id: bundle_risk for a in artifacts},
            bundle_level_risk_score=bundle_risk,
            guessed_source_identities=guessed,
            findings=findings_list,
            empty_evidence_count=0,
            malformed_response_count=0,
            direct_identifier_findings=di_findings,
            semantic_clue_findings=sc_findings,
            numeric_fingerprint_findings=nf_findings,
            release_recommendation=recommendation,
            blockers=blockers,
            warnings=warnings,
            succeeded=True,
        )

    def health_check(self) -> bool:
        return not self._missing_key

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def provider_kind(self) -> str:
        return self.PROVIDER_KIND

    @property
    def model_id(self) -> str:
        return self.MODEL_ID

    @property
    def model_version(self) -> str:
        return self.MODEL_VERSION


# ── Factory ────────────────────────────────────────────────────────────────


def create_review_provider(
    provider_type: str,
    config: dict[str, Any] | None = None,
) -> ReviewProvider:
    """Create a review provider from type and config."""
    cfg = config or {}
    if provider_type == "mock":
        return MockReviewProvider()
    elif provider_type == "nvidia":
        return NVIDIAReviewProvider(api_key=cfg.get("api_key"))
    else:
        raise ValueError(f"Unknown review provider type: {provider_type}")
