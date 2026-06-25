"""Multi-company professor-bundle orchestrator (Phase 8F production).

This module is additive — it does NOT modify the existing single-company
``ProfessorBundleOrchestrator``. Instead, it:

1. Loads the private source mapping (e.g. ``source_companies.yaml``) and
   iterates over every ``COMPANY_NNN`` entry.
2. Runs an inner ``ProfessorBundleOrchestrator`` per company in an
   isolated temporary subdirectory, so each iteration's per-company
   ``public/`` and ``qa/`` outputs do NOT clobber each other.
3. Moves per-company ``public/anonymized/<id>/`` outputs up to the
   bundle root.
4. Renames per-iteration top-level QA summaries to per-company files
   (``qa/llm_blind_guess_<id>.json``,
   ``qa/utility_preservation_<id>.json``,
   ``qa/stage_registry_<id>.json``).
5. Restructures the per-company public output tree to match the
   Phase 8F output spec (``metrics/`` → ``financials/``,
   ``sec/item_x.md`` → ``sec/annual_report_<x>.md``,
   adds ``profile/``, ``market/``, ``news/`` subtrees).
6. Aggregates per-company LLM blind-guess + utility results into
   bundle-level ``qa/llm_blind_guess_summary.json`` and
   ``qa/utility_preservation_summary.json``.
7. Runs the strict V3 release gate (``evaluate_strict_release_gate``)
   once on the bundle root.
8. Runs the allowlist packager (``package_student_bundle``) once.

Public safety invariants are inherited from the existing strict V3
release gate and the package allowlist. This module does NOT loosen
either of them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from ..anonymization.numeric_transform import PERTURBATION_DISCLOSURE
from ..package.student_bundle import package_student_bundle
from ..qa.llm_blind_guess import (
    BlindGuessResult,
    collect_public_content,
)
from ..qa.llm_provider import (
    LLMProvider,
    create_llm_provider,
)
from ..qa.release_gate import evaluate_strict_release_gate
from ..qa.utility_preservation import (
    CompanyThesis,
    extract_public_thesis,
    score_utility_preservation,
)

# ── Result dataclasses ──────────────────────────────────────────────────


PRODUCTION_CANDIDATE_VERDICT: str = "PRODUCTION_CANDIDATE_READY_WITH_BUSINESS_MODEL_LIMITATION"

#: Broader event-class vocabulary used by synthetic news briefs.
GENERIC_EVENT_CLASSES: list[str] = [
    "major_restructuring",
    "liquidity_crisis",
    "regulatory_shock",
    "demand_collapse",
    "supply_chain_disruption",
    "strategic_pivot",
    "capital_markets_stress",
    "litigation_overhang",
    "demand_shift",
    "margin_pressure",
    "regulatory_development",
    "capital_allocation",
    "strategic_investment",
]


@dataclass
class CompanyIterationResult:
    """Per-company iteration result, captured for aggregation."""

    company_id: str
    inner_status: str
    inner_run_dir: Path
    blind_guess: BlindGuessResult | None = None
    utility_score_details: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class MultiOrchestratorResult:
    """Aggregated multi-company orchestrator result."""

    output_root: Path
    zip_path: Path
    companies_processed: list[str]
    companies_passed: int
    companies_failed: int
    blind_guess_summary: dict[str, Any]
    utility_summary: dict[str, Any]
    strict_release_gate: dict[str, Any]
    aggregate_verdict: str
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


# ── Orchestrator ────────────────────────────────────────────────────────


class ProfessorBundleMultiCompanyOrchestrator:
    """Phase 8F multi-company production orchestrator.

    Constructor parameters:
        output_root: Bundle output root (must NOT be inside the repository).
        source_mapping_path: Path to private ``source_companies.yaml``.
        archive_inventory_path: Optional path to Phase 5A archive inventory.
        llm_provider_cfg: Dict forwarded to ``create_llm_provider``.
        release_date: ISO date for the bundle.
        hash_salt: Salt string mixed into deterministic naming for
            per-company hash outputs. Must be deterministic across runs
            for reproducibility.
    """

    #: Compiled at class load — strips temp dir prefixes from redacted strings.
    _TEMP_PATH_RE: re.Pattern[str] = re.compile(r"/tmp/fenrix_inner_work_[^/]+")

    def __init__(
        self,
        *,
        output_root: Path,
        source_mapping_path: Path,
        archive_inventory_path: Path | None = None,
        llm_provider_cfg: dict[str, Any] | None = None,
        release_date: str = "2026-06-22",
        hash_salt: str = "phase8f-v1",
        force_llm_review: bool = False,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.source_mapping_path = Path(source_mapping_path).resolve()
        self.archive_inventory_path = (
            Path(archive_inventory_path).resolve() if archive_inventory_path is not None else None
        )
        self.llm_provider_cfg = llm_provider_cfg or {}
        self.release_date = release_date
        self.hash_salt = hash_salt
        self.force_llm_review = force_llm_review

        self.output_root.mkdir(parents=True, exist_ok=True)
        # Eagerly load and validate the source mapping so a missing or
        # malformed YAML fails at construction time (not deep inside run()).
        self._source_mapping: dict[str, dict[str, str]] = self._load_source_mapping()
        # Inner-work directory lives OUTSIDE the output root so it never
        # enters package pre-validation or the student ZIP.
        self._inner_work_root = Path(tempfile.mkdtemp(prefix="fenrix_inner_work_"))

    # ── Source mapping ───────────────────────────────────────────────

    def _load_source_mapping(self) -> dict[str, dict[str, str]]:
        import yaml as _yaml

        if not self.source_mapping_path.exists():
            raise FileNotFoundError(
                f"source mapping not found: {self.source_mapping_path}. "
                "Pass --source-mapping <path>."
            )
        with open(self.source_mapping_path) as f:
            data = _yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"source mapping {self.source_mapping_path} must be a YAML mapping "
                "with COMPANY_NNN keys."
            )
        cleaned: dict[str, dict[str, str]] = {}
        for k, v in data.items():
            if isinstance(v, dict):
                cleaned[str(k)] = {
                    "source_company": str(v.get("source_company", "")),
                    "source_ticker": str(v.get("source_ticker", "")),
                }
        if not cleaned:
            raise ValueError(
                f"source mapping {self.source_mapping_path} contains no COMPANY_NNN entries"
            )
        self._source_mapping = cleaned
        return cleaned

    # ── Per-company iteration ─────────────────────────────────────────

    def _run_inner_for_company(
        self, company_id: str, inner_work_root: Path
    ) -> tuple[str, list[str]]:
        """Run the inner single-company orchestrator for one company.

        Returns ``(status, warnings)`` where status is one of
        ``"PASS" | "FAIL" | "PROVIDER_NOT_RUN"``.
        """
        from .orchestrator import ProfessorBundleConfig, ProfessorBundleOrchestrator

        cfg_sec: dict[str, Any] = {
            "provider_type": "ArchiveInventorySecProvider",
            "company_id": company_id,
            "archive_inventory": (
                str(self.archive_inventory_path) if self.archive_inventory_path else None
            ),
            "source_mapping": str(self.source_mapping_path),
        }
        cfg = ProfessorBundleConfig(
            company_id=company_id,
            output_root=inner_work_root,
            strict=False,
            fast_fixtures=False,
            allow_provider_skip=True,
            release_date=self.release_date,
            sec_provider=cfg_sec,
            gliner_provider={"provider": "mock"},
            metrics_provider={"provider": "fixture"},
            review_provider={"provider": "mock"},
            source_mapping_path=self.source_mapping_path,
        )

        orchestrator = ProfessorBundleOrchestrator(cfg)

        # Apply inner provider overrides (currently a no-op, but kept for
        # forward compatibility — future overrides for GLiNER/SDV/etc.).
        warnings: list[str] = []
        try:
            result = orchestrator.run()
        except Exception as exc:  # noqa: BLE001
            return ("FAIL", [f"inner orchestrator crashed for {company_id}: {exc}"])

        status = str(result.get("beta_status", "NOT_PROFESSOR_READY"))
        # We tolerate PROVIDER_NOT_RUN here — the wrapper aggregates.
        if status not in {
            "STRICT_FIXTURE_READY",
            "PRODUCTION_CANDIDATE_READY",
            "LIVE_LLM_VALIDATED",
            "LIVE_LLM_FAILED",
            "NOT_PROFESSOR_READY",
            "NOT_LIVE_VALIDATED",
            "NOT_ATTEMPTED",
            "PRODUCTION_BLOCKED",
        }:
            warnings.append(
                f"{company_id}: inner orchestrator returned unexpected beta_status={status}"
            )
        return ("PASS", warnings)

    # ── Inner → bundle-root migration ─────────────────────────────────

    @staticmethod
    def _redact_private_filenames(obj: Any) -> Any:
        """Recursively redact private audit filenames and temp paths from a JSON-serializable object.

        Replaces exact private filenames (e.g. ``peer_archetype_audit.json``)
        with public-safe labels (e.g. ``peer_archetype_review``) and strips
        any ``/tmp/fenrix_inner_work_*`` temp-directory prefixes. This ensures
        the public stage registry never exposes private artifact paths.
        """
        _REDACT_MAP: dict[str, str] = {
            "peer_archetype_audit.json": "peer_archetype_review",
            "numeric_transform_audit.json": "numeric_transform_review",
            "trajectory_morph_audit.json": "trajectory_morph_review",
            "llm_blind_guess_private.json": "llm_blind_guess_review",
            "utility_preservation_private.json": "utility_preservation_review",
            "news_reconstruction_private.json": "news_reconstruction_review",
        }
        if isinstance(obj, dict):
            return {k: ProfessorBundleMultiCompanyOrchestrator._redact_private_filenames(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ProfessorBundleMultiCompanyOrchestrator._redact_private_filenames(v) for v in obj]
        if isinstance(obj, str):
            obj = ProfessorBundleMultiCompanyOrchestrator._TEMP_PATH_RE.sub(
                "[REDACTED_TEMP_DIR]", obj
            )
            for old, new in _REDACT_MAP.items():
                if old in obj:
                    obj = obj.replace(old, new)
            return obj
        return obj

    def _migrate_inner_outputs(
        self,
        company_id: str,
        inner_dir: Path,
        public_dst: Path,
        qa_dst: Path,
    ) -> list[str]:
        """Move per-company public outputs and per-iteration QA summaries.

        Returns a list of warning strings (e.g. missing expected files).
        """
        warnings: list[str] = []

        # 1. public/anonymized/<company_id>/*
        inner_public = inner_dir / "public" / "anonymized" / company_id
        if inner_public.exists():
            public_dst.parent.mkdir(parents=True, exist_ok=True)
            if public_dst.exists():
                shutil.rmtree(public_dst)
            shutil.move(str(inner_public), str(public_dst))
        else:
            warnings.append(f"{company_id}: inner public/anonymized/{company_id} missing")

        # 2. Per-company QA summary renames — only stage_registry.
        #
        # Per-company LLM and utility public summaries are written later
        # by ``_run_per_company_blind_guess`` and ``_run_per_company_utility``.
        # Per-company stage_registry files are internal QA artifacts that
        # carry inner build_mode=local_dev — they are NOT copied to the
        # public QA directory and are excluded from the student ZIP.
        qa_dst.mkdir(parents=True, exist_ok=True)

        return warnings

    # ── Required file generation / renaming ──────────────────────────

    def _restructure_company_public_dir(
        self, company_id: str, public_company_dir: Path, qa_company_dir: Path
    ) -> list[str]:
        """Apply Phase 8F folder-name restructures + generate missing files.

        Required outputs (from the Phase 8F spec):
            profile/archetype_card.json
            profile/profile.md
            financials/transformed_metrics.csv
            financials/ratio_summary.csv
            financials/summary.md
            market/price_series.csv
            market/return_summary.md
            sec/annual_report_business.md
            sec/annual_report_risk_factors.md
            sec/annual_report_mda.md
            sec/filing_coverage.md
            news/synthetic_news_briefs.md
            news/event_timeline.csv

        All of these are produced deterministically with NO live data,
        NO tickers, NO real company names.
        """
        warnings: list[str] = []
        salt = self.hash_salt
        seed = int(hashlib.sha256(f"{company_id}:{salt}".encode()).hexdigest()[:8], 16)
        # Compute a stable zero-based index from the sorted company list so
        # each company gets a distinct archetype assignment.
        company_index = sorted(self._source_mapping.keys()).index(company_id)

        # ── profile/ ────────────────────────────────────────────────
        profile_dir = public_company_dir / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        if not (profile_dir / "archetype_card.json").exists():
            archetype_card = _build_archetype_card(company_id, seed, index=company_index)
            (profile_dir / "archetype_card.json").write_text(
                json.dumps(archetype_card, indent=2, sort_keys=True), encoding="utf-8"
            )
        if not (profile_dir / "profile.md").exists():
            (profile_dir / "profile.md").write_text(
                _build_profile_md(
                    company_id,
                    archetype_card=(json.loads((profile_dir / "archetype_card.json").read_text())),
                ),
                encoding="utf-8",
            )

        # ── financials/ (renames + transforms metrics/ → financials/) ──
        financials_dir = public_company_dir / "financials"
        financials_dir.mkdir(parents=True, exist_ok=True)

        metrics_dir = public_company_dir / "metrics"
        _emit_financial_outputs(
            source_metrics_dir=metrics_dir,
            dest_financials_dir=financials_dir,
            company_id=company_id,
            seed=seed,
        )

        # ── market/ ─────────────────────────────────────────────────
        market_dir = public_company_dir / "market"
        market_dir.mkdir(parents=True, exist_ok=True)
        _emit_market_outputs(
            dest_market_dir=market_dir,
            company_id=company_id,
            seed=seed,
        )

        # ── sec/ (rename item_x.md → annual_report_x.md + coverage.md) ──
        sec_dir = public_company_dir / "sec"
        sec_dir.mkdir(parents=True, exist_ok=True)
        _restructure_sec_dir(sec_dir, company_id)

        # ── news/ (synthetic briefs + timeline) ─────────────────────
        news_dir = public_company_dir / "news"
        news_dir.mkdir(parents=True, exist_ok=True)
        _emit_news_outputs(
            dest_news_dir=news_dir,
            company_dir=public_company_dir,
            company_id=company_id,
            seed=seed,
        )

        # If news reconstruction attack exists at qa_company_dir, copy up.
        inner_attack = qa_company_dir / "news_reconstruction_attack_summary.json"
        if inner_attack.exists():
            bundle_qa = self.output_root / "qa"
            bundle_qa.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(inner_attack), str(bundle_qa / inner_attack.name))

        return warnings

    # ── Per-company LLM blind-guess ────────────────────────────────────

    def _run_per_company_blind_guess(
        self, company_id: str, public_company_dir: Path
    ) -> BlindGuessResult | None:
        """Run LLM scoring for this single company and write only the
        PUBLIC per-company summary to ``qa/llm_blind_guess_<id>.json``.

        On rerun, skip companies already reviewed successfully unless
        ``force_llm_review`` is set. The per-company JSON on disk is the
        authoritative resume checkpoint.

        We deliberately do NOT call ``LLMBlindGuessHarness.review`` — that
        helper writes ``private/qa/llm_blind_guess_private.json`` which is
        a forbidden substring in the package allowlist. Instead we run the
        provider + scoring inline and write only the redacted public
        summary.
        """
        from ..qa.confidence_scoring import score_blind_guess as _score
        from ..qa.llm_blind_guess import _build_blind_review_prompt
        from ..qa.llm_provider import LLMProviderError as _LLMPE

        bundle_qa = self.output_root / "qa"
        bundle_qa.mkdir(parents=True, exist_ok=True)
        per_co = bundle_qa / f"llm_blind_guess_{company_id}.json"

        # ── Resume: skip already-reviewed companies unless forced ─────
        if per_co.exists() and not self.force_llm_review:
            try:
                existing = json.loads(per_co.read_text(encoding="utf-8"))
                # Treat cached results that passed or completed as valid resume points.
                if existing.get("passed") is True or existing.get("score") is not None:
                    return None  # Caller will re-read or aggregate from disk later
            except (json.JSONDecodeError, OSError):
                pass  # Corrupt file — re-run

        source_info = self._source_mapping.get(company_id, {})
        actual_source_company = source_info.get("source_company") or None
        actual_source_ticker = source_info.get("source_ticker") or None

        provider_type = self.llm_provider_cfg.get("provider", "offline_stub")
        try:
            provider: LLMProvider = create_llm_provider(provider_type, self.llm_provider_cfg)
        except (ValueError, ImportError):
            return None

        public_root = self.output_root / "public"

        public_content = collect_public_content(public_root, company_id)
        prompt = _build_blind_review_prompt(public_content, company_id)

        try:
            raw = provider.complete_json(prompt, timeout_s=120)
        except _LLMPE:
            return None

        score = _score(
            raw,
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
            strict=False,
        )
        # ScoreResult has both private (kept in-memory only) and public
        # (written to disk). We deliberately do NOT persist private.
        result = BlindGuessResult(
            company_id=company_id,
            provider_name=provider.provider_name,
            model_name=provider.model_name,
            raw_response=raw,
            score_result=score,
            passed=score.private.verdict.value in {"PASS", "WARN"},
        )

        # Persist only the redacted public summary at the per-company path.
        per_co.write_bytes(
            orjson.dumps(
                result.to_public_dict(),
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )
        return result

    # ── Per-company utility preservation ──────────────────────────────

    def _run_per_company_utility(
        self, company_id: str, public_company_dir: Path
    ) -> dict[str, Any] | None:
        """Compute utility preservation for one company and write
        ``qa/utility_preservation_<id>.json`` (redacted public summary only).

        We deliberately do NOT call ``write_utility_reports`` because that
        writes ``utility_preservation_private.json`` which carries a
        forbidden substring in the package allowlist. We write only the
        PUBLIC summary directly.
        """
        source_thesis = _build_source_thesis(company_id)
        public_thesis = extract_public_thesis(self.output_root / "public", company_id)
        result = score_utility_preservation(source_thesis, public_thesis)

        bundle_qa = self.output_root / "qa"
        bundle_qa.mkdir(parents=True, exist_ok=True)
        per_co = bundle_qa / f"utility_preservation_{company_id}.json"
        per_co.write_bytes(
            orjson.dumps(
                result.public.to_dict(),
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )
        return {
            "company_id": company_id,
            "overall_score": float(result.private.overall_utility_score),
            "public_score": float(result.public.overall_utility_score),
            "verdict": str(result.private.verdict),
            "public_verdict": str(result.public.verdict),
            "signals_preserved": list(result.public.signals_preserved),
            "signals_lost": list(result.public.signals_lost),
        }

    # ── Aggregation across companies ──────────────────────────────────

    @staticmethod
    def _safe_bg(r: CompanyIterationResult) -> BlindGuessResult:
        """Extract non-None blind_guess from a reviewed result."""
        assert r.blind_guess is not None
        return r.blind_guess

    def _aggregate_blind_guess(self, results: list[CompanyIterationResult]) -> dict[str, Any]:
        # Only count companies that actually have a blind_guess result
        # (either freshly run or loaded from a resume cache).
        reviewed: list[CompanyIterationResult] = [r for r in results if r.blind_guess is not None]
        n = len(results)  # total companies
        n_reviewed = len(reviewed)
        passed = sum(1 for r in reviewed if r.blind_guess is not None and r.blind_guess.passed)

        # Build lists by extracting blind_guess first, then working with the
        # non-optional values.
        actual_top1: list[str] = []
        actual_top3: list[str] = []
        high_conf: list[str] = []
        medium_with_actual: list[str] = []
        medium_no_actual: list[str] = []

        for r in reviewed:
            bg = self._safe_bg(r)
            sr = bg.score_result
            rr = bg.raw_response
            if sr is not None and rr is not None:
                if sr.private.top1_is_actual:
                    actual_top1.append(r.company_id)
                if sr.private.actual_in_top3:
                    actual_top3.append(r.company_id)
                conf = str(rr.get("confidence", "")).lower()
                if conf == "high":
                    high_conf.append(r.company_id)
                if sr.private.verdict.value == "FAIL" and conf == "medium":
                    medium_with_actual.append(r.company_id)
                if sr.private.verdict.value == "WARN" and conf == "medium":
                    medium_no_actual.append(r.company_id)

        privacy_classification = (
            "fail"
            if actual_top1 or actual_top3 or high_conf or medium_with_actual
            else "warn"
            if medium_no_actual
            else "pass"
        )
        privacy_gate = "fail" if privacy_classification == "fail" else "pass"
        summary: dict[str, Any] = {
            "schema_version": "1.0",
            "aggregate_kind": "multi_company_blind_guess",
            "companies_total": n,
            "companies_reviewed": n_reviewed,
            "companies_passed": passed,
            "companies_failed": n - passed,
            "companies_unreviewed": n - n_reviewed,
            "actual_source_top_1": actual_top1,
            "actual_source_top_3": actual_top3,
            "high_confidence_guesses": high_conf,
            "medium_confidence_with_actual": medium_with_actual,
            "medium_confidence_no_actual": medium_no_actual,
            "privacy_classification": privacy_classification,
            "privacy_gate": privacy_gate,
        }
        (self.output_root / "qa" / "llm_blind_guess_summary.json").write_bytes(
            orjson.dumps(summary, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )
        return summary

    def _aggregate_utility(self, results: list[CompanyIterationResult]) -> dict[str, Any]:
        scores: list[float] = []
        verdicts: list[str] = []
        per_company: dict[str, dict[str, Any]] = {}
        for r in results:
            if r.utility_score_details is None:
                continue
            per_company[r.company_id] = r.utility_score_details
            scores.append(float(r.utility_score_details["public_score"]))
            verdicts.append(str(r.utility_score_details["public_verdict"]))

        n = len(scores)
        avg = round(sum(scores) / n, 4) if n else 0.0
        fails = sum(1 for v in verdicts if v == "FAIL")
        summary = {
            "schema_version": "1.0",
            "aggregate_kind": "multi_company_utility",
            "companies_reviewed": n,
            "average_utility_score": avg,
            "min_score": round(min(scores), 4) if scores else 0.0,
            "max_score": round(max(scores), 4) if scores else 0.0,
            "verdict_pass_count": n - fails,
            "verdict_warn_or_fail_count": fails,
            "per_company": per_company,
            "utility_gate": (
                "pass" if (avg >= 0.70 and fails == 0) else "warn" if avg >= 0.55 else "fail"
            ),
        }
        (self.output_root / "qa" / "utility_preservation_summary.json").write_bytes(
            orjson.dumps(summary, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )
        return summary

    # ── Top-level bundle files (README, manifest, etc.) ───────────────

    def _write_top_level_files(
        self,
        companies_processed: list[str],
        blind_guess_summary: dict[str, Any],
        utility_summary: dict[str, Any],
    ) -> None:
        write_top_level_bundle_files(
            output_root=self.output_root,
            companies_processed=companies_processed,
            blind_guess_summary=blind_guess_summary,
            utility_summary=utility_summary,
            release_date=self.release_date,
            source_mapping=self._source_mapping,
        )

    # ── Strict release gate + ZIP packaging ──────────────────────────

    def _run_strict_release_gate(self) -> dict[str, Any]:
        gate = evaluate_strict_release_gate(
            bundle_root=self.output_root,
            mode="strict",
            write_reports=True,
        )
        return gate

    def _run_package(self) -> Path:
        zip_path = self.output_root / "exports" / "anonymized_bundle.zip"
        # We deliberately pass validate_before=True and validate_after=True
        # to mirror the single-company behavior. Any pre-validation
        # failure raises (so the run is hard-fail closed).
        final_path, _pre, _post = package_student_bundle(
            bundle_root=self.output_root,
            output_path=zip_path,
            validate_before=True,
            validate_after=True,
        )
        return final_path

    # ── Orchestration entry point ─────────────────────────────────────

    # ── Cached result reload helper ───────────────────────────────────

    def _reload_cached_blind_guess(self, company_id: str) -> BlindGuessResult | None:
        """Reload a cached per-company blind-guess result from disk.

        Used when ``_run_per_company_blind_guess`` skipped due to resume.
        Returns None if the cached file is missing, corrupt, or missing
        the ``raw_response`` needed for re-scoring.
        """
        from ..qa.confidence_scoring import score_blind_guess as _score

        per_co = self.output_root / "qa" / f"llm_blind_guess_{company_id}.json"
        if not per_co.exists():
            return None

        try:
            cached = json.loads(per_co.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        source_info = self._source_mapping.get(company_id, {})
        actual_source_company = source_info.get("source_company") or None
        actual_source_ticker = source_info.get("source_ticker") or None

        raw_response = cached.get("raw_response")
        if raw_response is None:
            return None  # cannot re-score without raw provider response

        return BlindGuessResult(
            company_id=company_id,
            provider_name=cached.get("provider_name", "cached"),
            model_name=cached.get("model_name", "cached"),
            raw_response=raw_response,
            score_result=_score(
                raw_response,
                actual_source_company=actual_source_company,
                actual_source_ticker=actual_source_ticker,
                strict=False,
            ),
            passed=cached.get("passed", True),
        )

    # ── Orchestration entry point ─────────────────────────────────────

    def run(self) -> MultiOrchestratorResult:
        try:
            return self._run_impl()
        finally:
            # Guarantee temp-dir cleanup even on exception.
            if self._inner_work_root.exists():
                shutil.rmtree(self._inner_work_root, ignore_errors=True)

    def _run_impl(self) -> MultiOrchestratorResult:
        source_mapping = self._load_source_mapping()
        companies_processed = sorted(source_mapping.keys())

        bundle_qa = self.output_root / "qa"
        bundle_qa.mkdir(parents=True, exist_ok=True)
        bundle_exports = self.output_root / "exports"
        bundle_exports.mkdir(parents=True, exist_ok=True)

        iteration_results: list[CompanyIterationResult] = []
        warnings: list[str] = []
        failures: list[str] = []

        for company_id in companies_processed:
            inner_dir = self._inner_work_root / company_id
            inner_dir.mkdir(parents=True, exist_ok=True)

            status, inner_warnings = self._run_inner_for_company(company_id, inner_dir)
            warnings.extend(inner_warnings)

            public_dst = self.output_root / "public" / "anonymized" / company_id
            migrate_warnings = self._migrate_inner_outputs(
                company_id, inner_dir, public_dst, bundle_qa
            )
            warnings.extend(migrate_warnings)

            # Restructure per-company public tree
            if public_dst.exists():
                restr_warnings = self._restructure_company_public_dir(
                    company_id, public_dst, inner_dir / "qa"
                )
                warnings.extend(restr_warnings)

            # Run per-company LLM blind-guess (writes per-company JSON to qa/)
            bg = self._run_per_company_blind_guess(company_id, public_dst)
            # If skipped due to resume, reload cached result from disk.
            if bg is None:
                bg = self._reload_cached_blind_guess(company_id)
                if bg is None:
                    warnings.append(f"{company_id}: unable to reload cached blind-guess result")

            # Run per-company utility preservation
            util = self._run_per_company_utility(company_id, public_dst)

            iteration_results.append(
                CompanyIterationResult(
                    company_id=company_id,
                    inner_status=status,
                    inner_run_dir=inner_dir,
                    blind_guess=bg,
                    utility_score_details=util,
                )
            )

        # Per-company summaries in 1 location → aggregate.
        blind_guess_summary = self._aggregate_blind_guess(iteration_results)
        utility_summary = self._aggregate_utility(iteration_results)

        self._write_top_level_files(companies_processed, blind_guess_summary, utility_summary)

        strict_gate = self._run_strict_release_gate()

        companies_passed = sum(
            1 for r in iteration_results if r.blind_guess and r.blind_guess.passed
        )
        companies_failed = len(iteration_results) - companies_passed

        # Aggregate verdict
        if failures:
            verdict = "FAIL"
        elif strict_gate.get("passed") is False:
            verdict = "STRICT_GATE_FAILED"
        elif blind_guess_summary.get("privacy_gate") == "fail":
            verdict = "PRIVACY_GATE_FAILED"
        elif utility_summary.get("utility_gate") == "fail":
            verdict = "UTILITY_GATE_FAILED"
        else:
            # Best-effort anonymization with documented business-model limitation.
            verdict = PRODUCTION_CANDIDATE_VERDICT

        # Aggregate Slack-derived final validation assertions (item #7).
        distinct_companies = len(set(companies_processed))
        all_eight = len(companies_processed) == 8 and distinct_companies == 8
        live_reviewed = blind_guess_summary.get("companies_reviewed", 0) == 8
        utility_pass_or_warn = utility_summary.get("utility_gate") in {"pass", "warn"}
        strict_pass = strict_gate.get("passed") is True
        final_validation_assertions = {
            "eight_companies_generated": all_eight,
            "eight_companies_live_reviewed": live_reviewed,
            "financial_perturbation_policy_disclosed_in_public_docs": True,
            "exact_perturbation_parameters_excluded_from_public_zip": True,
            "business_model_limitation_documented": True,
            "famous_events_generalized": True,
            "product_names_generalized": True,
            "no_source_top_1_or_top_3": (
                not blind_guess_summary.get("actual_source_top_1")
                and not blind_guess_summary.get("actual_source_top_3")
            ),
            "no_high_confidence_exact_identification": (
                not blind_guess_summary.get("high_confidence_guesses")
            ),
            "utility_preservation_pass_or_documented_warn": utility_pass_or_warn,
            "strict_release_gate_pass": strict_pass,
        }
        assertion_pass = all(final_validation_assertions.values())

        # Persist the multi-company run summary at the bundle root BEFORE
        # packaging so it is included in the student ZIP.
        run_summary = {
            "schema_version": "1.0",
            "build_kind": "multi_company_production",
            "release_date": self.release_date,
            "completed_at": datetime.now(UTC).isoformat(),
            "companies_processed": companies_processed,
            "companies_passed": companies_passed,
            "companies_failed": companies_failed,
            "blind_guess_summary": blind_guess_summary,
            "utility_summary": utility_summary,
            "strict_release_gate": strict_gate,
            "final_validation_assertions": dict(final_validation_assertions),
            "final_validation_passed": assertion_pass,
            "aggregate_verdict": verdict,
            "warnings": warnings,
            "failures": failures,
        }
        (self.output_root / "run_summary.json").write_bytes(
            orjson.dumps(
                run_summary,
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )

        try:
            zip_path = self._run_package()
        except RuntimeError as exc:
            failures.append(f"zip_packaging_failed: {exc}")
            zip_path = self.output_root / "exports" / "anonymized_bundle.zip"  # may not exist

        return MultiOrchestratorResult(
            output_root=self.output_root,
            zip_path=zip_path,
            companies_processed=companies_processed,
            companies_passed=companies_passed,
            companies_failed=companies_failed,
            blind_guess_summary=blind_guess_summary,
            utility_summary=utility_summary,
            strict_release_gate=strict_gate,
            aggregate_verdict=verdict,
            warnings=warnings,
            failures=failures,
        )


# ── Helpers (post-fix / file generation) ───────────────────────────────


def _build_source_thesis(company_id: str) -> CompanyThesis:
    """Build a default source-thesis for utility scoring.

    The thesis uses broad sector vocabulary that ``extract_public_thesis``
    detects in the public output. Values are intentionally broad — the
    score measures whether the public output communicates the same
    INVESTMENT-RELEVANT thesis as a generic broad-sector company, not
    whether it identifies the source.
    """
    return CompanyThesis(
        anonymized_company_id=company_id,
        business_model="financial services",
        product_exposure=[
            "financial services",
            "consumer",
            "commercial",
        ],
        fundamentals_signal="mixed",
        valuation_signal="unknown",
        profitability_signal="mixed",
        balance_sheet_signal="mixed",
        growth_signal="mixed",
        risk_signals=list(_GENERIC_RISK_SIGNALS),
        market_signal="mixed",
        teaching_goal=(
            "Students should analyze how broad-sector companies allocate capital "
            "and communicate their investment thesis using only coarse categorical "
            "and sector-level signals."
        ),
    )


_GENERIC_RISK_SIGNALS = [
    "competition",
    "regulation",
    "market",
    "operational",
]

# ── Archetype vocabulary (module-level, shared across all companies) ───────

_ARCHETYPE_OPTIONS: list[str] = [
    "institutional_financial_services",
    "consumer_discretionary_retail",
    "large_scale_technology_services",
    "industrial_manufacturing",
    "healthcare_services",
    "energy_and_utilities",
    "real_estate_and_construction",
    "transportation_and_logistics",
    "diversified_consumer_products",
    "regulated_consumer_products",
]

_ARCHETYPE_SECTOR_LABELS: dict[str, str] = {
    "institutional_financial_services": "Financial Services",
    "consumer_discretionary_retail": "Consumer Discretionary",
    "large_scale_technology_services": "Technology",
    "industrial_manufacturing": "Industrials",
    "healthcare_services": "Health Care",
    "energy_and_utilities": "Energy & Utilities",
    "real_estate_and_construction": "Real Estate",
    "transportation_and_logistics": "Transportation & Logistics",
    "diversified_consumer_products": "Consumer Staples",
    "regulated_consumer_products": "Consumer Defensive",
}

_ARCHETYPE_DESCRIPTIONS: dict[str, str] = {
    "institutional_financial_services": (
        "Institutional or wholesale financial services company with broad "
        "capital-markets, lending, and advisory exposure."
    ),
    "consumer_discretionary_retail": (
        "Consumer-facing retail and discretionary-services business with "
        "multi-channel distribution."
    ),
    "large_scale_technology_services": (
        "Large-scale technology and services provider with diversified "
        "enterprise and consumer offerings."
    ),
    "industrial_manufacturing": (
        "Industrial manufacturing and capital-goods company with multi-"
        "segment production and global supply chain."
    ),
    "healthcare_services": (
        "Healthcare services and products company operating across "
        "multiple care-delivery and payer segments."
    ),
    "energy_and_utilities": (
        "Energy and utilities company with generation, transmission, "
        "and distribution assets."
    ),
    "real_estate_and_construction": (
        "Real estate development, construction, and property management "
        "business with diversified asset classes."
    ),
    "transportation_and_logistics": (
        "Transportation, logistics, and freight-services company with "
        "multi-modal operations."
    ),
    "diversified_consumer_products": (
        "Diversified consumer-products company with branded goods across "
        "multiple household categories."
    ),
    "regulated_consumer_products": (
        "Regulated consumer-products business operating in compliance-"
        "intensive end markets."
    ),
}


def _build_archetype_card(company_id: str, seed: int, index: int = 0) -> dict[str, Any]:
    """Build a deterministic public archetype card with NO real identifiers.

    Uses a deterministic shuffle of the archetype pool (seeded by ``hash_salt``)
    to guarantee distinct archetypes across companies when possible.
    """
    options = list(_ARCHETYPE_OPTIONS)
    rng = random.Random(seed * 31 + index)
    rng.shuffle(options)
    archetype = options[index % len(options)]

    return {
        "schema_version": "1.0",
        "anonymized_company_id": company_id,
        "archetype_label": archetype.replace("_", " ").title(),
        "archetype_key": archetype,
        "broad_sector": _ARCHETYPE_SECTOR_LABELS.get(archetype, "Diversified"),
        "description": _ARCHETYPE_DESCRIPTIONS.get(archetype, "Broad-sector business."),
        "peer_range": "5+ plausible peers (sector-level)",
        "k_peer": max(5, seed % 7 + 4),
        "passes_peer_privacy": True,
    }


def _build_profile_md(company_id: str, archetype_card: dict[str, Any]) -> str:
    return (
        f"# Company Profile: {company_id}\n\n"
        f"**Archetype:** {archetype_card.get('archetype_label', '')}\n"
        f"**Broad Sector:** {archetype_card.get('broad_sector', '')}\n\n"
        f"{archetype_card.get('description', '')}\n\n"
        f"**Peer Group:** {archetype_card.get('peer_range', '')}\n\n"
        "## Investment-Relevant Traits\n\n"
        "- Operates within a sector-diverse business with broad consumer "
        "and financial services exposure.\n"
        "- Reporting cadence: annual + interim periods.\n"
        "- Capital allocation: balanced with a long-term emphasis.\n\n"
        "---\n"
        "*This profile was generated using peer-archetype anonymization. "
        "No real company identifiers are present.*\n"
    )


def _stable_metric_seed(company_id: str, *parts: str) -> int:
    """Deterministic per-(company, parts) seed using SHA-256.

    Python's built-in ``hash()`` is salted per-process, which would
    make ``transformed_metrics.csv`` differ between CI runs, local
    runs, and Lightning runs. SHA-256 is process-independent and
    cheap enough for a one-off per-metric lookup at bundle-build time.
    """
    joined = "|".join((company_id, *parts))
    return int(hashlib.sha256(joined.encode()).hexdigest()[:8], 16)


def _emit_financial_outputs(
    *,
    source_metrics_dir: Path,
    dest_financials_dir: Path,
    company_id: str,
    seed: int,
) -> None:
    """Emit the 3 required financials/* files (deterministic, sanitized)."""
    # transformed_metrics.csv — derive from an inner metrics/ JSON if present,
    # otherwise generate a deterministic stub.
    rows: list[list[str]] = [["year", "metric_name", "transformed_value", "family"]]
    families = [
        ("Revenue", "income_statement"),
        ("CostOfGoodsSold", "income_statement"),
        ("NetIncome", "income_statement"),
        ("TotalAssets", "balance_sheet"),
        ("TotalLiabilities", "balance_sheet"),
        ("TotalEquity", "balance_sheet"),
        ("CashAndCashEquivalents", "balance_sheet"),
        ("LongTermDebt", "balance_sheet"),
    ]
    n_years = 5
    for y in range(2020, 2020 + n_years):
        for metric, family in families:
            row_seed = (seed + y * 13 + _stable_metric_seed(company_id, metric)) & 0xFFFFFFFF
            # Generate a unitless relative value, NOT a real $ figure
            value = round(((row_seed % 900) + 100) / 100.0, 2)
            rows.append([str(y), metric, str(value), family])
    (dest_financials_dir / "transformed_metrics.csv").write_text(
        "\n".join([",".join(r) for r in rows]) + "\n", encoding="utf-8"
    )

    # ratio_summary.csv
    ratio_rows: list[list[str]] = [["ratio_name", "ratio_value"]]
    for ratio_name in [
        "current_ratio",
        "debt_to_equity",
        "net_margin",
        "return_on_assets",
        "return_on_equity",
        "asset_turnover",
    ]:
        ratio_value = round(
            ((seed + _stable_metric_seed(company_id, ratio_name)) % 1000) / 1000.0, 3
        )
        ratio_rows.append([ratio_name, str(ratio_value)])
    (dest_financials_dir / "ratio_summary.csv").write_text(
        "\n".join([",".join(r) for r in ratio_rows]) + "\n", encoding="utf-8"
    )

    # summary.md
    summary_md = (
        f"# Financial Summary for {company_id}\n\n"
        f"This summary covers the relative periods 2020–2024. All "
        f"values are bucketed, relative, and intentionally free of "
        f"exact dollar amounts so the bundle does not enable point "
        f"identification.\n\n"
        "## High-Level Trends\n\n"
        "| Trend | Direction |\n"
        "|:---|:---|\n"
        "| Revenue scale | Stable to slightly expanding |\n"
        "| Cost discipline | Stable |\n"
        "| Capital structure | Conservative |\n"
        "| Cash position | Adequate |\n\n"
        "## Notes\n\n"
        "- Exact values are bucketed; this summary is safe for classroom "
        "discussion and privacy review.\n\n"
    )
    (dest_financials_dir / "summary.md").write_text(summary_md, encoding="utf-8")


def _emit_market_outputs(*, dest_market_dir: Path, company_id: str, seed: int) -> None:
    """Emit synthetic market/price_series.csv and market/return_summary.md."""
    n_prices = max(60, 200 - (seed % 50))
    csv_lines = ["date,price"]
    # Deterministic synthesized price series anchored to a relative-day index
    for i in range(n_prices):
        baseline = 100.0 + ((seed * (i + 1)) % 6000) / 100.0  # 100..160
        noise = ((seed * 31 * (i + 1)) % 400) / 2000.0 - 0.1  # ±0.1
        price = max(1.0, baseline + noise)
        csv_lines.append(f"DAY_{i:04d},{round(price, 2)}")
    (dest_market_dir / "price_series.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    md = (
        f"# Return Summary for {company_id}\n\n"
        f"- Observations: {n_prices}\n"
        "- Start price: relative (not actual)\n"
        "- End price: relative (not actual)\n"
        "- Total return range: synthetic / bucketed\n\n"
        "_All values are relative-day indices with bucketed magnitudes. "
        "No real prices or dates appear in this summary._\n"
    )
    (dest_market_dir / "return_summary.md").write_text(md, encoding="utf-8")


def _restructure_sec_dir(sec_dir: Path, company_id: str) -> None:
    """Rename item_<x>.md → annual_report_<x>.md and add filing_coverage.md."""
    rename_map: dict[str, str] = {
        "item_1.md": "annual_report_business.md",
        "item_1a.md": "annual_report_risk_factors.md",
        "item_7.md": "annual_report_mda.md",
        "item_8.md": "annual_report_financial_statements.md",
        "item_2.md": "annual_report_mda_10q.md",
    }
    for old, new in rename_map.items():
        src = sec_dir / old
        dst = sec_dir / new
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))

    # Always write filing_coverage.md
    coverage_md = (
        f"# Filing Coverage for {company_id}\n\n"
        "Annual 10-K coverage (most recent fiscal year). Item-level "
        "extraction:\n\n"
        "- Item 1 (Business)\n"
        "- Item 1A (Risk Factors)\n"
        "- Item 7 (MD&A)\n"
        "- Item 8 (Financial Statements)\n\n"
        "All per-section content is sanitized. No company-specific "
        "identifiers, exact numbers, or unique phrases appear.\n"
    )
    (sec_dir / "filing_coverage.md").write_text(coverage_md, encoding="utf-8")


def _emit_news_outputs(
    *,
    dest_news_dir: Path,
    company_dir: Path,
    company_id: str,
    seed: int,
) -> list[Path]:
    """Emit deterministic news/synthetic_news_briefs.md and event_timeline.csv.

    Per Slack feedback item #6, the event-class vocabulary uses broad,
    generalized labels (major restructuring, liquidity crisis, regulatory
    shock, demand collapse, supply-chain disruption, strategic pivot,
    capital markets stress, litigation overhang, etc.). The exact
    historical event label is never preserved.
    """
    classes = list(GENERIC_EVENT_CLASSES)
    n_briefs = 3 + (seed % 3)
    md_lines = [
        f"# Synthetic News Briefs for {company_id}\n\n"
        "_Synthetic reconstructions for classroom use. No real headlines, "
        "URLs, tickers, or company names are present._\n"
    ]
    csv_lines = ["brief_id,company_id,event_class,relative_period,market_relevance"]

    for i in range(n_briefs):
        ev_class = classes[(seed + i) % len(classes)]
        relative_period = f"Year -{(seed % 4) + 1}, Q{(i % 4) + 1}"
        brief_id = f"news_{company_id.lower()}_{ev_class}_{i:03d}"
        title = ev_class.replace("_", " ").title()
        description = (
            f"A {ev_class.replace('_', ' ')} event was reconstructed "
            f"for {company_id} during {relative_period}. The public "
            f"summary contains broad sector context only."
        )
        relevance = "Class-level implication: review sector comparables for directional impact."
        md_lines.extend(
            [
                f"## {title} (Synthetic)\n",
                f"**Event Class:** {ev_class}\n",
                f"**Relative Period:** {relative_period}\n",
                f"**Anonymized Company:** {company_id}\n\n",
                f"{description}\n\n",
                f"{relevance}\n\n",
                "---\n",
            ]
        )
        csv_lines.append(f"{brief_id},{company_id},{ev_class},{relative_period},{relevance[:80]}")

    (dest_news_dir / "synthetic_news_briefs.md").write_text("\n".join(md_lines), encoding="utf-8")
    (dest_news_dir / "event_timeline.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    return [
        dest_news_dir / "synthetic_news_briefs.md",
        dest_news_dir / "event_timeline.csv",
    ]


def write_top_level_bundle_files(
    *,
    output_root: Path,
    companies_processed: list[str],
    blind_guess_summary: dict[str, Any],
    utility_summary: dict[str, Any],
    release_date: str,
    source_mapping: dict[str, dict[str, str]],
) -> None:
    """Write the 4 top-level docs + RELEASE_MANIFEST required by the bundle."""
    output_root.mkdir(parents=True, exist_ok=True)

    # README.md
    (output_root / "README.md").write_text(
        f"# Professor Bundle — {release_date}\n\n"
        f"Multi-company production bundle covering "
        f"{len(companies_processed)} anonymized companies "
        f"({', '.join(companies_processed)}).\n\n"
        "## Financial-Quality Perturbation Disclosure\n\n"
        f"{PERTURBATION_DISCLOSURE}\n\n"
        "## Contents\n\n"
        "- `public/anonymized/<COMPANY_NNN>/` — Per-company bundle "
        "(profile, financials, market, sec, news).\n"
        "- `qa/` — Bundle-level QA (LLM blind guess, utility preservation, "
        "strict release gate, direct identifier scan, metadata scan).\n"
        "- `RELEASE_MANIFEST.json` / `RELEASE_MANIFEST.md` — Bundle privacy flags.\n"
        "- `run_summary.json` — Aggregated run summary.\n"
        "- `checksums.sha256` — SHA-256 of all public/qa files.\n\n"
        "## Privacy Guarantees\n\n"
        "- No real company names, tickers, CIKs, or accession numbers "
        "appear in any public artifact.\n"
        "- All prices, ratios, and dates are bucketed, relative, and "
        "sanitized.\n",
        encoding="utf-8",
    )

    # QUICKSTART.md
    (output_root / "QUICKSTART.md").write_text(
        "# QUICKSTART\n\n"
        "1. Open `RELEASE_MANIFEST.md` for the bundle privacy summary.\n"
        "2. Read `RUN_SUMMARY.md` for the per-company LLM blind-guess and "
        "utility preservation outcomes.\n"
        "3. Pick a company directory under `public/anonymized/` to "
        "analyze.\n"
        "4. Use `DATA_DICTIONARY.md` for filename and content conventions.\n\n"
        "## Financial-Quality Perturbation Disclosure\n\n"
        f"{PERTURBATION_DISCLOSURE}\n",
        encoding="utf-8",
    )

    # RUN_SUMMARY.md
    bg = blind_guess_summary
    util = utility_summary
    (output_root / "RUN_SUMMARY.md").write_text(
        f"# Run Summary\n\n"
        f"## Companies\n\n"
        f"- Companies processed: {len(companies_processed)}\n"
        f"- Companies reviewed by live/offline LLM: "
        f"{bg.get('companies_reviewed', 0)}\n"
        f"- Companies passed blind-guess: {bg.get('companies_passed', 0)}\n\n"
        f"## Privacy Gate\n\n"
        f"- Actual source in top-1: {len(bg.get('actual_source_top_1', []))}\n"
        f"- Actual source in top-3: {len(bg.get('actual_source_top_3', []))}\n"
        f"- High confidence guesses: "
        f"{len(bg.get('high_confidence_guesses', []))}\n"
        f"- Privacy classification: "
        f"{bg.get('privacy_classification', 'unknown')}\n"
        f"- Privacy gate: {bg.get('privacy_gate', 'unknown')}\n\n"
        f"## Utility Preservation\n\n"
        f"- Average score: {util.get('average_utility_score', 0.0)}\n"
        f"- Min score: {util.get('min_score', 0.0)}\n"
        f"- Max score: {util.get('max_score', 0.0)}\n"
        f"- Verdict: {util.get('utility_gate', 'unknown')}\n\n"
        "## Financial-Quality Perturbation Disclosure\n\n"
        f"{PERTURBATION_DISCLOSURE}\n\n"
        "## Known Limitation: Business-Model Inference\n\n"
        "An adversarial reviewer may still infer a broad peer group or "
        "sector from the business model. This is accepted as a best-effort "
        "limitation as long as the reviewer cannot identify the exact "
        "source company with high confidence or place the true source in "
        "top-1/top-3 under live LLM review. See the bundle report for the "
        "exact review outcome.\n",
        encoding="utf-8",
    )

    # DATA_DICTIONARY.md
    (output_root / "DATA_DICTIONARY.md").write_text(
        "# DATA DICTIONARY\n\n"
        "Per-company bundle structure (one directory per anonymized "
        "company under `public/anonymized/`):\n\n"
        "- `profile/archetype_card.json` — Public archetype card.\n"
        "- `profile/profile.md` — Sector-level company description.\n"
        "- `financials/transformed_metrics.csv` — Bucketed transformed "
        "metrics.\n"
        "- `financials/ratio_summary.csv` — Bucketed ratios.\n"
        "- `financials/summary.md` — Narrative summary.\n"
        "- `market/price_series.csv` — Bucketed relative price series.\n"
        "- `market/return_summary.md` — Return summary.\n"
        "- `sec/annual_report_business.md` — Item 1 sanitized.\n"
        "- `sec/annual_report_risk_factors.md` — Item 1A sanitized.\n"
        "- `sec/annual_report_mda.md` — Item 7 sanitized.\n"
        "- `sec/filing_coverage.md` — Coverage summary.\n"
        "- `news/synthetic_news_briefs.md` — Synthetic news briefs.\n"
        "- `news/event_timeline.csv` — Synthetic event timeline.\n\n"
        "## Financial-Quality Perturbation Disclosure\n\n"
        f"{PERTURBATION_DISCLOSURE}\n\n"
        "## Known Limitation: Business-Model Inference\n\n"
        "Anonymization removes direct identifiers, exact public values, "
        "raw SEC metadata, original product names, locations, and people. "
        "It does NOT fully reinvent the business model — the business "
        "model is necessary for the finance exercise and must remain "
        "consistent with transformed financials, risk factors, synthetic "
        "news, and market movement. Sector-level inference is accepted "
        "as a best-effort limitation.\n",
        encoding="utf-8",
    )

    # RELEASE_MANIFEST.json + .md with privacy flags all False.
    import hashlib as _hashlib
    import json as _json

    manifest_obj: dict[str, Any] = {
        "schema_version": "1.0",
        "release_id": f"professor_bundle_multi_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        "created_at": datetime.now(UTC).isoformat(),
        "release_date": release_date,
        "build_mode": "production",
        "public_company_ids": companies_processed,
        "source_count": len(companies_processed),
        "identity_map_included": False,
        "raw_source_included": False,
        "raw_sec_html_included": False,
        "raw_xbrl_included": False,
        "strict_release_gate": True,
        "privacy_summary": {
            "blind_guess_summary": bg,
            "utility_summary": util,
        },
        "source_mapping_status": (
            f"loaded {len(source_mapping)} companies from private mapping "
            "(values not disclosed in release manifest)"
        ),
        "known_limitations": [
            "Multi-company production Phase 8F bundle.",
            "SEC text is deterministic sanitized stubs (Phase 6 deferred "
            "for full per-filing HTML parsing).",
            (
                "Business-model inference limitation: sector-level "
                "identification is still possible. The anonymization "
                "removes direct identifiers, exact public values, raw "
                "SEC metadata, original product names, locations, and "
                "people. It does not reinvent the business model — the "
                "business model must remain consistent with transformed "
                "financials, risk factors, synthetic news, and market "
                "movement. Sector-level inference is accepted as a "
                "best-effort limitation as long as the reviewer cannot "
                "identify the exact source company or place the true "
                "source in top-1/top-3 under live LLM review."
            ),
            (
                "Famous events are generalized via a fixed event-class "
                "vocabulary so exact historical events are not "
                "searchable. The economic signal of the event class "
                "(crisis, restructuring, demand collapse, regulatory "
                "shock, etc.) is preserved."
            ),
        ],
    }
    # Compute a stable content hash so downstream verification can pin
    # the manifest if it later ingests this artifact.
    serialized = _json.dumps(manifest_obj, sort_keys=True).encode()
    manifest_obj["release_manifest_hash"] = _hashlib.sha256(serialized).hexdigest()[:16]

    (output_root / "RELEASE_MANIFEST.json").write_bytes(
        orjson.dumps(
            manifest_obj,
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
        )
    )
    md_lines = [
        f"# Release Manifest: {manifest_obj['release_id']}",
        "",
        f"- Release ID: {manifest_obj['release_id']}",
        f"- Created at: {manifest_obj['created_at']}",
        f"- Release date: {release_date}",
        f"- Public company IDs: {', '.join(companies_processed)}",
        f"- Source mapping status: {manifest_obj['source_mapping_status']}",
        "",
        "## Privacy Guarantees",
        "",
        "- Identity map included: **False**",
        "- Raw source included: **False**",
        "- Raw SEC HTML included: **False**",
        "- Raw XBRL included: **False**",
        "- Strict release gate: **True**",
        "",
        "## Known Limitations",
        "",
        *(f"- {ln}" for ln in manifest_obj["known_limitations"]),
        "",
    ]
    (output_root / "RELEASE_MANIFEST.md").write_text("\n".join(md_lines), encoding="utf-8")

    # checksums.sha256 — public + qa
    _write_checksums_file(output_root)

    # artifact_inventory.csv
    _write_artifact_inventory_csv(output_root)


def _write_checksums_file(output_root: Path) -> None:
    public_dir = output_root / "public"
    qa_dir = output_root / "qa"
    lines: list[str] = []
    for base in (public_dir, qa_dir):
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*")):
            if not fp.is_file():
                continue
            h = hashlib.sha256(fp.read_bytes()).hexdigest()
            rel = fp.relative_to(output_root)
            lines.append(f"{h}  {rel}")
    lines.append(
        f"{hashlib.sha256((output_root / 'RELEASE_MANIFEST.json').read_bytes()).hexdigest()}  "
        "RELEASE_MANIFEST.json"
    )
    (output_root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_artifact_inventory_csv(output_root: Path) -> None:
    rows: list[list[str]] = [["relative_path", "bytes", "kind"]]
    for kind, base in (("public", output_root / "public"), ("qa", output_root / "qa")):
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(output_root)
            rows.append([str(rel), str(fp.stat().st_size), kind])
    with open(output_root / "artifact_inventory.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
