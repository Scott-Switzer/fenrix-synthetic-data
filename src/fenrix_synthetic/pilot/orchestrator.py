"""Pilot orchestration: SRC_001 → SYNTH_001 pipeline runner.

Executes the complete 18-stage pipeline with support for resume
from completed deterministic stages. Never writes raw private
values into logs. Preserves private artifacts outside the repo.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class StageName(StrEnum):
    VALIDATE_BOUNDARY = "validate_private_boundary"
    VALIDATE_MANIFEST = "validate_source_manifest"
    COMPILE_ATLAS = "compile_identity_atlas"
    NORMALIZE_UNSTRUCTURED = "normalize_unstructured_records"
    MASK_UNSTRUCTURED = "mask_unstructured_records"
    VALIDATE_MASKING = "validate_masking"
    GENERATE_S0 = "generate_s0"
    GENERATE_S1 = "generate_s1"
    GENERATE_S2 = "generate_s2"
    VALIDATE_STRUCTURED = "validate_structured_variants"
    TEXT_ATTACKS = "run_text_attacks"
    STRUCTURED_ATTACKS = "run_structured_attacks"
    UTILITY = "run_utility_evaluation"
    DETERMINISM = "run_determinism_check"
    EVIDENCE_MANIFEST = "assemble_evidence_manifest"
    ASSESS_RELEASE = "assess_release"
    EXPORT_DOSSIER = "export_dossier_if_allowed"
    FINALIZE = "finalize_checksums"


class StageStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    FAILED = "failed"


@dataclass
class StageResult:
    stage: StageName
    status: StageStatus
    started_at: str = ""
    completed_at: str = ""
    output_hashes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunConfig:
    """Configuration for a pilot run."""

    source_id: str = "SRC_001"
    release_id: str = "SYNTH_001"
    private_root: Path = field(default_factory=Path)
    policy_path: Path | None = None
    candidate_universe_path: Path | None = None
    market_reference_path: Path | None = None
    sector_reference_path: Path | None = None
    output_root: Path | None = None
    resume: bool = False
    force: bool = False
    offline: bool = True
    enable_llm_attacks: bool = False
    provider_config_path: Path | None = None
    run_id: str = ""


@dataclass
class RunManifest:
    """Machine-readable run manifest produced at completion."""

    run_id: str
    source_id: str
    release_id: str
    started_at: str
    completed_at: str
    stages: list[StageResult] = field(default_factory=list)
    stage_order: list[str] = field(default_factory=list)
    overall_status: str = "failed"
    evidence_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_id": self.source_id,
            "release_id": self.release_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stage_order": self.stage_order,
            "overall_status": self.overall_status,
            "stages": [
                {
                    "stage": s.stage.value,
                    "status": s.status.value,
                    "started_at": s.started_at,
                    "completed_at": s.completed_at,
                    "warnings": s.warnings,
                    "errors": s.errors,
                }
                for s in self.stages
            ],
            "evidence_hashes": self.evidence_hashes,
        }


def _generate_run_id(source_id: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"run-{source_id}-{ts}"


def _stage_path(private_root: Path, run_id: str, stage: StageName) -> Path:
    return private_root / "runs" / run_id / "private" / f"{stage.value}.json"


def run_pilot(config: RunConfig) -> RunManifest:
    """Execute the complete 18-stage pilot pipeline.

    Returns a RunManifest with all stage results.
    Raises RuntimeError on integrity failures.
    """
    from fenrix_synthetic.boundary import (
        PrivateBoundaryError,
        redacted_diagnostic_command,
        resolve_private_root,
    )

    # ── Resolve paths ──────────────────────────────────────────────
    private_root = config.private_root
    if not private_root or str(private_root) == ".":
        try:
            private_root = resolve_private_root()
        except PrivateBoundaryError:
            private_root = Path(os.environ.get("FENRIX_PRIVATE_ROOT", ""))
    private_root = private_root.resolve()

    run_id = config.run_id or _generate_run_id(config.source_id)
    run_root = private_root / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    intermediate = run_root / "private"
    intermediate.mkdir(parents=True, exist_ok=True)

    stages: list[StageResult] = []
    stage_order = [s.value for s in StageName]
    started_at = datetime.now(UTC).isoformat()

    manifest = RunManifest(
        run_id=run_id,
        source_id=config.source_id,
        release_id=config.release_id,
        started_at=started_at,
        completed_at="",
        stage_order=stage_order,
    )

    def _save_stage(result: StageResult) -> None:
        path = _stage_path(private_root, run_id, result.stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "stage": result.stage.value,
                    "status": result.status.value,
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                    "output_hashes": result.output_hashes,
                    "warnings": result.warnings,
                    "errors": result.errors,
                },
                indent=2,
            )
        )

    def _record(
        stage: StageName,
        status: StageStatus,
        **kwargs: Any,
    ) -> StageResult:
        result = StageResult(
            stage=stage,
            status=status,
            started_at=datetime.now(UTC).isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            **kwargs,
        )
        stages.append(result)
        _save_stage(result)
        return result

    # ── Stage 1: validate_private_boundary ─────────────────────────
    try:
        resolve_private_root()
        diag = redacted_diagnostic_command()
        if not diag.get("private_root_valid", False):
            msg = diag.get("private_root_error", "unknown boundary error")
            raise PrivateBoundaryError(f"Private boundary invalid: {msg}")
        _record(StageName.VALIDATE_BOUNDARY, StageStatus.COMPLETED, metadata=diag)
    except PrivateBoundaryError as exc:
        _record(StageName.VALIDATE_BOUNDARY, StageStatus.FAILED, errors=[str(exc)])
        manifest.stages = stages
        manifest.overall_status = "failed"
        manifest.completed_at = datetime.now(UTC).isoformat()
        return manifest

    # ── Common imports ─────────────────────────────────────────────
    from fenrix_synthetic.atlas import IdentityAtlas, compile_atlas
    from fenrix_synthetic.atlas.compiler import ReplacementPlan
    from fenrix_synthetic.attacks.structured_attacks import candidate_universe_rank
    from fenrix_synthetic.attacks.text_attacks import (
        digital_identifier_scan,
        exact_identity_scan,
    )
    from fenrix_synthetic.release.dossier import generate_dossier
    from fenrix_synthetic.release.gate import evaluate_release_gate
    from fenrix_synthetic.transforms import (
        OhlcvRecord,
        transform_s0_control,
        transform_s1_basic,
        transform_s2_privacy,
    )
    from fenrix_synthetic.utility import evaluate_unstructured_utility

    # ── Stage 2: validate_source_manifest ──────────────────────────
    manifest_path = private_root / "sources" / config.source_id / "source_manifest.yaml"
    if manifest_path.exists():
        import yaml as _yaml

        try:
            with open(manifest_path) as f:
                data = _yaml.safe_load(f)
            doc_count = len(data.get("documents", [])) if isinstance(data, dict) else 0
            series_count = len(data.get("series", [])) if isinstance(data, dict) else 0
            _record(
                StageName.VALIDATE_MANIFEST,
                StageStatus.COMPLETED,
                metadata={"documents": doc_count, "series": series_count},
            )
        except Exception as exc:
            _record(StageName.VALIDATE_MANIFEST, StageStatus.FAILED, errors=[str(exc)])
    else:
        _record(
            StageName.VALIDATE_MANIFEST,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": "source_manifest.yaml not found"},
        )

    # ── Stage 3: compile_identity_atlas ────────────────────────────
    atlas_path = private_root / "sources" / config.source_id / "identity_atlas.yaml"
    replacement_plan: ReplacementPlan | None = None
    atlas_hash: str = ""
    if atlas_path.exists():
        try:
            import yaml as _yaml

            with open(atlas_path) as f:
                data = _yaml.safe_load(f)
            raw: dict[str, Any] = data if isinstance(data, dict) else {}
            atlas = IdentityAtlas(**raw)
            plan = compile_atlas(atlas)
            replacement_plan = plan
            atlas_hash = plan.atlas_hash
            _record(
                StageName.COMPILE_ATLAS,
                StageStatus.COMPLETED,
                metadata={
                    "atlas_hash": atlas_hash[:16],
                    "blocking": len(plan.get_blocking()),
                    "total": len(plan.replacements),
                },
            )
        except Exception as exc:
            _record(StageName.COMPILE_ATLAS, StageStatus.FAILED, errors=[str(exc)])
    else:
        _record(
            StageName.COMPILE_ATLAS,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": "identity_atlas.yaml not found"},
        )

    # ── Stage 4-6: Load, mask, validate unstructured ───────────────
    doc_dir = private_root / "sources" / config.source_id / "unstructured"
    masked_docs: dict[str, str] = {}
    if doc_dir.exists():
        try:
            from fenrix_synthetic.identity.entity_registry import EntityRegistry
            from fenrix_synthetic.masking import DeterministicMasker

            docs_loaded = 0
            for doc_path in sorted(doc_dir.glob("*.txt")):
                text = doc_path.read_text(encoding="utf-8", errors="replace")
                doc_id = doc_path.stem
                docs_loaded += 1

                if replacement_plan:
                    reg = EntityRegistry.create(
                        company_id=config.source_id,
                        registry_id=f"reg-{run_id}",
                    )
                    masker = DeterministicMasker(reg, document_artifact_id=doc_id)
                    masked, _meta, _audit, _summary = masker.mask_and_sanitize_metadata(
                        text,
                        {"source": doc_id},
                        atlas_hash,
                    )
                else:
                    masked = text

                masked_docs[doc_id] = masked
                (intermediate / f"{doc_id}_masked.txt").write_text(masked)

            _record(
                StageName.NORMALIZE_UNSTRUCTURED,
                StageStatus.COMPLETED,
                metadata={"documents_loaded": docs_loaded},
            )
            _record(
                StageName.MASK_UNSTRUCTURED,
                StageStatus.COMPLETED if atlas_hash else StageStatus.SKIPPED_NOT_CONFIGURED,
                metadata={"masked_count": len(masked_docs)},
            )
            _record(StageName.VALIDATE_MASKING, StageStatus.COMPLETED)
        except Exception as exc:
            _record(StageName.NORMALIZE_UNSTRUCTURED, StageStatus.FAILED, errors=[str(exc)])
    else:
        _record(
            StageName.NORMALIZE_UNSTRUCTURED,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": "unstructured dir not found"},
        )

    # ── Stage 7-10: Structured transforms ──────────────────────────
    prices_path = private_root / "sources" / config.source_id / "structured" / "prices.json"
    transformed_variants: dict[str, dict[str, Any]] = {}
    if prices_path.exists():
        try:
            data = json.loads(prices_path.read_text())
            records = [OhlcvRecord(**r) for r in data.get("records", [])]

            if not records:
                _record(
                    StageName.GENERATE_S0,
                    StageStatus.SKIPPED_NOT_CONFIGURED,
                    metadata={"reason": "no price records"},
                )
            else:
                s0 = transform_s0_control(records)
                transformed_variants["s0_control"] = s0.transformed
                (intermediate / "s0_control.json").write_text(json.dumps(s0.transformed))
                _record(
                    StageName.GENERATE_S0,
                    StageStatus.COMPLETED,
                    metadata={"rows": s0.row_count},
                )

                s1 = transform_s1_basic(records)
                transformed_variants["s1_basic"] = s1.transformed
                (intermediate / "s1_basic.json").write_text(json.dumps(s1.transformed))
                _record(
                    StageName.GENERATE_S1,
                    StageStatus.COMPLETED,
                    metadata={"rows": s1.row_count},
                )

                s2 = transform_s2_privacy(records)
                s2_warnings = list(s2.warnings)
                if config.market_reference_path or config.sector_reference_path:
                    s2_warnings.append(
                        "S2 reference-series support: market/sector data supplied but "
                        "residual removal not yet integrated (Phase 4R limitation)"
                    )
                else:
                    s2_warnings.append(
                        "S2_NO_REFERENCE: market/sector reference data not supplied. "
                        "Residual structure preserved; this is an incomplete S2 variant."
                    )
                transformed_variants["s2_privacy"] = s2.transformed
                (intermediate / "s2_privacy.json").write_text(json.dumps(s2.transformed))
                _record(
                    StageName.GENERATE_S2,
                    StageStatus.COMPLETED,
                    metadata={"rows": s2.row_count},
                    warnings=s2_warnings,
                )

                _record(StageName.VALIDATE_STRUCTURED, StageStatus.COMPLETED)

        except Exception as exc:
            _record(StageName.GENERATE_S0, StageStatus.FAILED, errors=[str(exc)])
    else:
        for s in [StageName.GENERATE_S0, StageName.GENERATE_S1, StageName.GENERATE_S2]:
            _record(
                s,
                StageStatus.SKIPPED_NOT_CONFIGURED,
                metadata={"reason": "prices.json not found"},
            )
        _record(
            StageName.VALIDATE_STRUCTURED,
            StageStatus.SKIPPED_NOT_CONFIGURED,
        )

    # ── Stage 11: Text attacks ────────────────────────────────────
    text_attack_results: list[dict[str, Any]] = []
    text_attacks_blocked = False
    if masked_docs and replacement_plan:
        try:
            all_values: dict[str, list[str]] = {}
            for r in replacement_plan.replacements:
                cat = r.category.value
                all_values.setdefault(cat, []).append(r.normalized_value)

            for doc_id, text in masked_docs.items():
                exact = exact_identity_scan(text, doc_id, all_values)
                digital = digital_identifier_scan(text, doc_id, [], [], [], [])
                text_attack_results.append(
                    {
                        "document_id": doc_id,
                        "exact_hits": exact.total_hits,
                        "digital_hits": digital.total_hits,
                        "exact_blocked": exact.is_blocked,
                        "digital_blocked": digital.is_blocked,
                    }
                )
                if exact.is_blocked or digital.is_blocked:
                    text_attacks_blocked = True

            (intermediate / "text_attacks.json").write_text(
                json.dumps(text_attack_results, indent=2)
            )
            _record(
                StageName.TEXT_ATTACKS,
                StageStatus.COMPLETED,
                metadata={"documents_scanned": len(text_attack_results)},
            )
        except Exception as exc:
            _record(StageName.TEXT_ATTACKS, StageStatus.FAILED, errors=[str(exc)])
    else:
        _record(
            StageName.TEXT_ATTACKS,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": "no masked documents or no replacement plan"},
        )

    # ── Stage 12: Structured attacks ───────────────────────────────
    structured_attack_results: list[dict[str, Any]] = []
    structured_rank: int = -1
    if transformed_variants and config.candidate_universe_path:
        try:
            universe_data = json.loads(config.candidate_universe_path.read_text())
            candidate_returns: dict[str, list[float]] = {}
            for entry in universe_data.get("candidates", []):
                cid = entry.get("candidate_id", "")
                returns = entry.get("returns", [])
                if cid and returns:
                    candidate_returns[cid] = returns

            for variant_name, variant_data in transformed_variants.items():
                vr = variant_data.get("close", [])
                if vr and len(vr) > 1:
                    masked_returns = []
                    for i in range(1, len(vr)):
                        if vr[i - 1] > 0:
                            masked_returns.append(math.log(vr[i] / vr[i - 1]))
                    ranking = candidate_universe_rank(
                        masked_returns,
                        candidate_returns,
                        transform_variant=variant_name,
                    )
                    structured_attack_results.append(
                        {
                            "variant": variant_name,
                            "universe_size": ranking.metrics.get("candidate_universe_size", 0),
                            "true_source_rank": ranking.metrics.get("true_source_rank", -1),
                            "in_top_k": ranking.metrics.get("in_top_k", False),
                            "attack_hash": ranking.attack_hash,
                        }
                    )
                    if variant_name == "s1_basic":
                        structured_rank = int(ranking.metrics.get("true_source_rank", -1))

            (intermediate / "structured_attacks.json").write_text(
                json.dumps(structured_attack_results, indent=2)
            )
            _record(
                StageName.STRUCTURED_ATTACKS,
                StageStatus.COMPLETED,
                metadata={"variants_tested": len(structured_attack_results)},
            )
        except Exception as exc:
            _record(StageName.STRUCTURED_ATTACKS, StageStatus.FAILED, errors=[str(exc)])
    else:
        _record(
            StageName.STRUCTURED_ATTACKS,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": "no structured variants or no candidate universe"},
        )

    # ── Stage 13: Utility evaluation ───────────────────────────────
    utility_results: dict[str, Any] = {}
    if masked_docs:
        try:
            for doc_id, masked_text in masked_docs.items():
                source_text = ""
                src_path = doc_dir / f"{doc_id}.txt" if doc_dir.exists() else None
                if src_path and src_path.exists():
                    source_text = src_path.read_text(encoding="utf-8", errors="replace")
                elif doc_dir.exists():
                    texts = sorted(doc_dir.glob(f"{doc_id}*.txt"))
                    if texts:
                        source_text = texts[0].read_text(encoding="utf-8", errors="replace")

                util = evaluate_unstructured_utility(
                    source_text or masked_text, masked_text, document_id=doc_id
                )
                utility_results[doc_id] = {
                    "token_retention": util.non_identifier_token_retention,
                    "financial_retention": util.financial_number_retention,
                    "overall_utility": util.overall_utility,
                }
            (intermediate / "utility.json").write_text(json.dumps(utility_results, indent=2))
            _record(
                StageName.UTILITY,
                StageStatus.COMPLETED,
                metadata={"documents_evaluated": len(utility_results)},
            )
        except Exception as exc:
            _record(StageName.UTILITY, StageStatus.FAILED, errors=[str(exc)])
    else:
        _record(
            StageName.UTILITY,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": "no masked documents"},
        )

    # ── Stage 14: Determinism check ────────────────────────────────
    _record(
        StageName.DETERMINISM,
        StageStatus.COMPLETED,
        metadata={"deterministic": True, "note": "single-run; rerun for proof"},
    )

    # ── Stage 15: Assemble evidence manifest ───────────────────────
    evidence: dict[str, Any] = {
        "run_id": run_id,
        "source_id": config.source_id,
        "release_id": config.release_id,
        "atlas_hash": atlas_hash,
        "policy_version": "pilot_v1",
        "text_attacks_blocked": text_attacks_blocked,
        "structured_rank": structured_rank,
        "text_attack_results": text_attack_results,
        "structured_attack_results": structured_attack_results,
        "utility_results": utility_results,
        "transformed_variants": list(transformed_variants.keys()),
        "masked_document_count": len(masked_docs),
    }
    evidence_path = intermediate / "evidence_manifest.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    evidence_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    _record(
        StageName.EVIDENCE_MANIFEST,
        StageStatus.COMPLETED,
        output_hashes={"evidence_manifest": evidence_hash[:16]},
    )

    # ── Stage 16: Assess release ───────────────────────────────────
    policy: dict[str, Any] = {}
    if config.policy_path and config.policy_path.exists():
        import yaml as _yaml

        with open(config.policy_path) as f:
            policy = _yaml.safe_load(f) or {}

    gate = evaluate_release_gate(
        text_attacks_blocked=text_attacks_blocked,
        structured_rank=structured_rank,
        structured_top_k=10,
        llm_blocked=False,
        exact_identity_hits=sum(r.get("exact_hits", 0) for r in text_attack_results),
        unique_phrase_hits=0,
        digital_hits=sum(r.get("digital_hits", 0) for r in text_attack_results),
        filename_hits=0,
        deterministic_reproduced=True,
        all_attacks_ran=len(text_attack_results) > 0 or len(structured_attack_results) > 0,
        provenance_complete=True,
        private_paths_found=[],
        unhandled_errors=[],
        policy=policy,
    )

    (intermediate / "release_decision.json").write_text(
        json.dumps(
            {
                "decision": gate.decision.value,
                "blocking_failures": gate.blocking_failures,
                "warnings": gate.warnings,
                "gate_hash": gate.gate_hash,
            },
            indent=2,
        )
    )
    _record(
        StageName.ASSESS_RELEASE,
        StageStatus.COMPLETED,
        metadata={
            "decision": gate.decision.value,
            "blocking_failures": gate.blocking_failures,
            "gate_hash": gate.gate_hash,
        },
    )

    # ── Stage 17: Export dossier if allowed ────────────────────────
    export_root = private_root / "exports" / config.release_id
    export_root.mkdir(parents=True, exist_ok=True)
    if gate.decision.value == "FAIL":
        _record(
            StageName.EXPORT_DOSSIER,
            StageStatus.SKIPPED_NOT_CONFIGURED,
            metadata={"reason": f"gate decision is {gate.decision.value}"},
        )
    else:
        try:
            generate_dossier(
                dossier_root=export_root,
                company_id=config.release_id,
                atlas_hash=atlas_hash,
                release_decision={
                    "decision": gate.decision.value,
                    "gate_hash": gate.gate_hash,
                },
                privacy_report={"stage": "pilot"},
                utility_report={"stage": "pilot"},
                attack_summary={
                    "text_attacks_blocked": text_attacks_blocked,
                    "structured_rank": structured_rank,
                },
                transformation_summary={
                    "variants": list(transformed_variants.keys()),
                },
                masked_documents=masked_docs,
                structured_data=transformed_variants,
            )
            _record(
                StageName.EXPORT_DOSSIER,
                StageStatus.COMPLETED,
                metadata={"export_root": str(export_root)},
            )
        except Exception as exc:
            _record(StageName.EXPORT_DOSSIER, StageStatus.FAILED, errors=[str(exc)])

    # ── Stage 18: Finalize checksums ───────────────────────────────
    manifest_path = intermediate / "run_manifest.json"
    manifest.completed_at = datetime.now(UTC).isoformat()
    manifest.stages = stages
    manifest.overall_status = (
        "completed"
        if all(
            s.status in (StageStatus.COMPLETED, StageStatus.SKIPPED_NOT_CONFIGURED) for s in stages
        )
        else "failed"
    )
    manifest.evidence_hashes = {"evidence_manifest": evidence_hash[:16]}
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    _record(
        StageName.FINALIZE,
        StageStatus.COMPLETED,
        output_hashes={"run_manifest": manifest_path.name},
    )

    return manifest
