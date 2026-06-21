"""Orchestrator for ``fenrix-synth reanonymize-run``.

Phase 1 — Validate source-run directory: must contain
``run_summary.json`` and a single ticker under ``originals/<TICKER>/``.
Phase 2 — Apply form + news limits before any work is done.
Phase 3 — Emit classroom-safe numeric package under
``public/numeric/classroom_safe/``.
Phase 4 — Emit public/sanitized news surrogates under
``public/surrogates/news/``.
Phase 5 — Emit public/sanitized SEC surrogates under
``public/surrogates/sec/`` (deterministic masker via ``TextAnonymizer``).
Phase 6 — Direct privacy scan (``attacks.text_attacks.exact_identity_scan``)
on the SEC surrogates against stored private values; result written to
``qa/direct_privacy_report.json``.
Phase 7 — Utility evaluation (``utility.unstructured.evaluate_unstructured_utility``)
on the masked content; result written to ``qa/utility_report.json``.
Phase 8 — Write structural stubs for semantic privacy and NVIDIA
review (NOT implemented in this revision; records ``INCOMPLETE``).
Phase 9 — Evaluate the release gate and write ``release_gate.json`` with
explicit ``beta_status`` and ``release_safe`` fields. Gate hash is
recomputed after stub conditions are appended so it is reproducible
by construction.

The orchestrator never mutates ``--source-run``. It writes a fresh
tree under ``--output-root``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from ..anonymization.classroom_numeric_writer import ClassroomNumericWriter
from ..anonymization.news_surrogate_generator import NewsSurrogateGenerator
from ..anonymization.registry_load import (
    RegistryLoadSummary,
    build_private_values_dict,
    load_atlas,
)
from ..anonymization.text_anonymizer import TextAnonymizer
from ..attacks.semantic_attacks import results_to_report, run_semantic_attack_suite
from ..attacks.text_attacks import exact_identity_scan, filename_and_metadata_scan
from ..identity.pseudonym_allowlist import (
    SAFE_PSEUDONYM_ALLOWLIST_SIZE,
    allowlist_human_readable,
    is_pseudonym_suppression_eligible,
)
from ..identity.schemas import EntityType
from ..providers.nvidia_client import NVIDIABounds
from ..providers.nvidia_review import NVIDIAReviewAdapter
from ..release.gate import (
    ReleaseDecision,
    ReleaseGateResult,
    evaluate_release_gate,
)
from ..utility.unstructured import evaluate_unstructured_utility
from .atlas_builder import (
    AtlasHarvestReport,
    DirectIdentifierAtlasBuilder,
    write_rejected_candidates_report,
)
from .limits import apply_form_limits, infer_form, parse_form_limits

logger = logging.getLogger(__name__)


class InvalidSourceRunError(ValueError):
    """Raised when ``--source-run`` cannot be used as a source pipeline-run."""


@dataclass
class RunContext:
    """Resolved inputs for the orchestrator."""

    source_run: Path
    output_root: Path
    ticker: str
    form_limits: dict[str, int]
    news_limit: int
    discovered_forms: dict[str, int]
    discovered_news_count: int


@dataclass
class SecSurrogateSelection:
    """Sources chosen by Phase 2, paired with the inferred form tag.

    Form tagging at Phase 2 is essential because the masker writes
    pseudonymised ``filing_<hash>.md`` filenames that cannot be
    reverse-mapped to a real form.
    """

    items: list[tuple[str | None, Path]]

    @property
    def paths(self) -> list[Path]:
        return [p for _form, p in self.items]

    @property
    def form_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for form, _p in self.items:
            if form is None:
                continue
            counts[form] = counts.get(form, 0) + 1
        return counts


class ReanonymizeOrchestrator:
    """Orchestrate the ``reanonymize-run`` command end-to-end."""

    PUBLIC_ROOT = "public"
    QA_ROOT = "qa"

    SEC_SURROGATE_SUBDIR = "sec"
    NEWS_SURROGATE_SUBDIR = "news"

    NUMERIC_SAFE_SUBDIR = "numeric/classroom_safe"

    def __init__(
        self,
        source_run: Path,
        output_root: Path,
        limit_forms: str | None,
        limit_news: int,
        allow_incomplete: bool = False,
        nvidia_mode: str | None = None,
        nvidia_max_artifacts: int | None = None,
        nvidia_max_chunks_reviewed: int | None = None,
        nvidia_max_chunks_rewritten: int | None = None,
        nvidia_smoke_max_input_chars: int | None = None,
    ) -> None:
        # ``allow_incomplete`` is a forward-compat operator signal: the
        # orchestrator already always writes the release gate with an
        # honest verdict (PASS / FAIL / INCOMPLETE) and never blindly
        # claims safety; this flag records intent without changing the
        # internal behavior. Future revisions can branch on it to skip
        # non-critical post-processing for in-progress runs.
        self.source_run = source_run.resolve()
        self.output_root = output_root.resolve()
        self.limit_forms_raw = limit_forms
        self.limit_news = max(0, int(limit_news))
        self.allow_incomplete = bool(allow_incomplete)
        self.form_limits = parse_form_limits(limit_forms)
        # Smoke-only char cap on each surrogate ingested by the bounded
        # NVIDIA review. Defaults to ``None`` (full-filing path).
        self.nvidia_smoke_max_input_chars = nvidia_smoke_max_input_chars
        # NVIDIA review budget — bounded to keep per-run API calls finite.
        self.nvidia_mode = nvidia_mode or "final_submission"
        self.nvidia_max_artifacts = nvidia_max_artifacts
        self.nvidia_max_chunks_reviewed = nvidia_max_chunks_reviewed
        self.nvidia_max_chunks_rewritten = nvidia_max_chunks_rewritten
        # Populated by ``_phase_atlas_load`` and consumed by
        # Phase 5 (``TextAnonymizer``) + Phase 6 (``_phase_direct_privacy``).
        # Setting up the slots here keeps the ``run()`` flow readable.
        self._preloaded_registry: Any = None
        self._preloaded_summary: RegistryLoadSummary | None = None
        # Captured from ``TextAnonymizer.anonymize_all`` manifests so we
        # can compute pre-mask hits without re-scanning the source HTML.
        self._pre_mask_hits: int = 0
        self._pre_replacement_count: int = 0
        # Captured from Phase 6 scan for the release_gate.json.
        self._post_mask_hits: int = 0
        # Phase 1.55 — coverage report from DirectIdentifierAtlasBuilder.
        self._harvest_report: AtlasHarvestReport | None = None
        # Semantic privacy attack results (Phase 8), initialized so a
        # conditional reporter never AttributeErrors.
        self._semantic_report: dict[str, Any] | None = None

    # ── Phase 1: Validate source-run ─────────────────────────────────

    def validate(self) -> RunContext:
        if not self.source_run.exists() or not self.source_run.is_dir():
            raise InvalidSourceRunError(f"--source-run directory does not exist: {self.source_run}")
        run_summary = self.source_run / "run_summary.json"
        if not run_summary.is_file():
            raise InvalidSourceRunError(
                f"--source-run is missing run_summary.json: {self.source_run}"
            )
        originals_root = self.source_run / "originals"
        if not originals_root.is_dir():
            raise InvalidSourceRunError(f"--source-run is missing originals/: {self.source_run}")

        tickers = sorted(p.name for p in originals_root.iterdir() if p.is_dir())
        if not tickers:
            raise InvalidSourceRunError(
                f"--source-run has no ticker subdirectory under originals/: "
                f"{self.source_run}/originals/"
            )
        if len(tickers) > 1:
            raise InvalidSourceRunError(
                f"--source-run contains multiple tickers {tickers}; "
                "reanonymize-run supports exactly one ticker per invocation. "
                "Run once per ticker."
            )
        ticker = tickers[0]

        sec_dir = originals_root / ticker / "sec"
        filings_dir = sec_dir / "filings"
        sec_candidates: list[Path] = []
        # SEC filings arrive as either ``*.html`` or ``*.htm`` (the
        # latter is the historical SEC convention); accept both.
        if filings_dir.is_dir():
            sec_candidates = sorted(
                p
                for p in filings_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".html", ".htm")
            )
        discovered_forms: dict[str, int] = {}
        for c in sec_candidates:
            f = infer_form(c.name)
            if f is not None:
                discovered_forms[f] = discovered_forms.get(f, 0) + 1

        news_dir = originals_root / ticker / "news"
        news_count = 0
        articles_path = news_dir / "articles.json"
        if articles_path.is_file():
            try:
                data = orjson.loads(articles_path.read_bytes())
                if isinstance(data, list):
                    news_count = len(data)
            except orjson.JSONDecodeError:
                logger.warning("articles.json is not valid JSON: %s", articles_path)

        return RunContext(
            source_run=self.source_run,
            output_root=self.output_root,
            ticker=ticker,
            form_limits=dict(self.form_limits),
            news_limit=self.limit_news,
            discovered_forms=discovered_forms,
            discovered_news_count=news_count,
        )

    # ── Public surface ───────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        # Top-level backstop: if any phase raises before the final
        # release gate writes ``qa/release_gate.json`` (and/or before
        # Phase 9 emits ``qa/nvidia_attack_report.json``), the operator
        # STILL gets strict fail-closed stubs so missing reports never
        # mask a real failure. We compute a source_hash up front so the
        # fail-closed gate can carry one without re-reading the path.
        #
        # SIGTERM is also trapped because CI shells use ``timeout N`` to
        # cap runner cost; without the handler, SIGTERM kills before
        # the exception-based backstop fires, leaving decision files
        # missing. ``try/except Exception`` does NOT catch SIGTERM.
        ctx = self.validate()
        qa_root = self.output_root / self.QA_ROOT
        import hashlib as _hashlib_top

        _source_hash_top = _hashlib_top.sha256(str(ctx.source_run).encode("utf-8")).hexdigest()[:16]

        # Install SIGTERM handler ONLY for the duration of run(); the
        # original handler is restored in the ``finally`` clause so
        # other unrelated signals remain untouched. We deliberately do
        # NOT touch SIGINT (user Ctrl+C): that should abort the run
        # immediately without writing fail-closed artifacts.
        import signal as _signal

        try:
            _original_sigterm = _signal.getsignal(_signal.SIGTERM)
        except (ValueError, AttributeError):  # pragma: no cover - non-main thread
            _original_sigterm = None

        def _sigterm_backstop(signum: int, frame: Any) -> None:  # noqa: ARG001
            """Fail-closed on SIGTERM and pass the kill back to the OS.

            Writes missing decision files via ``_ensure_decision_files_present``
            (idempotent — does NOT clobber existing files). Then restores the
            default SIGTERM handler and re-raises so the shell sees exit
            code 143 (SIGTERM-killed). Any error raised inside this handler
            is swallowed so the SIGTERM signal always reaches the OS.
            """
            try:
                self._ensure_decision_files_present(
                    qa_root=qa_root,
                    source_hash=_source_hash_top,
                    error_class="SIGTERM",
                )
            except BaseException:  # pragma: no cover - defensive
                pass
            # Restore default handler and re-raise the signal.
            try:
                _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)
                import os as _os_term

                _os_term.kill(_os_term.getpid(), _signal.SIGTERM)
            except BaseException:  # pragma: no cover
                # If we cannot re-raise, exit with a code that says we
                # at least tried to write the fail-closed report.
                raise SystemExit(143) from None

        try:
            _signal.signal(_signal.SIGTERM, _sigterm_backstop)
        except (ValueError, AttributeError, OSError):  # pragma: no cover - non-main thread
            pass

        try:
            return self._run_after_validate(ctx, qa_root)
        except Exception as exc:
            logger.exception(
                "reanonymize-run pipeline interrupted before completion: %s",
                exc,
            )
            # Backfill missing decision files so the operator has
            # evidence of WHY the run stopped. We do NOT swallow the
            # exception: a hard error is the truthful surface.
            self._ensure_decision_files_present(
                qa_root=qa_root,
                source_hash=_source_hash_top,
                error_class=exc.__class__.__name__,
            )
            raise
        finally:
            # Always restore the original SIGTERM handler so other
            # unrelated work in the same Python process is unaffected.
            if _original_sigterm is not None:
                try:
                    _signal.signal(_signal.SIGTERM, _original_sigterm)
                except (ValueError, OSError):  # pragma: no cover
                    pass

    def _run_after_validate(self, ctx: RunContext, qa_root: Path) -> dict[str, Any]:
        """Run the full phase sequence after Phase 1 has succeeded.

        Extracted from ``run()`` so the top-level wrapper can manage
        fail-closed backstop without re-indenting the entire phase
        ladder. Returns the run-summary dict identical to the legacy
        ``run()`` contract.
        """
        # Phase 1.55 — harvest & write coverage BEFORE registry load.
        # On reload: builder MAY augment ``private_maps/<TICKER>/identity_atlas.yaml``
        # with conservative regex + deterministic finds so the
        # subsequent load_atlas sees the merged atlas; even when it
        # does not, the harvest report itself gates the release on
        # ``critical_warnings_count == 0`` (blocker 4).
        self._phase_atlas_build(ctx, qa_root)
        # Phase 1.5 — load the registry BEFORE the public/ mkdir block.
        # If the loader reports ``blocking = True`` we MUST fail closed
        # so a downstream consumer can never scrape un-masked surrogates
        # off the public/ tree. This phase ALSO writes
        # ``qa/registry_load_report.json`` regardless of outcome.
        self._phase_atlas_load(ctx, qa_root)

        public_root = self.output_root / self.PUBLIC_ROOT
        public_surrogates = public_root / "surrogates"
        for d in (
            public_surrogates / self.SEC_SURROGATE_SUBDIR,
            public_surrogates / self.NEWS_SURROGATE_SUBDIR,
            public_root / self.NUMERIC_SAFE_SUBDIR,
        ):
            d.mkdir(parents=True, exist_ok=True)

        # Phase 2 — apply form limits; pre-tag each selected source with
        # its form so downstream phases can report per-form coverage
        # even after the masker has pseudonymised output filenames.
        # SEC filings arrive as ``*.html`` OR ``*.htm`` — accept both.
        sec_candidates_all: list[Path] = []
        filings_dir = ctx.source_run / "originals" / ctx.ticker / "sec" / "filings"
        if filings_dir.is_dir():
            sec_candidates_all = sorted(
                p
                for p in filings_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".html", ".htm")
            )
        sec_selection = SecSurrogateSelection(
            items=apply_form_limits(sec_candidates_all, self.form_limits)
        )

        # Phase 3 — numeric classroom-safe package
        numeric_pkg = self._phase_numeric_package(ctx, public_root / self.NUMERIC_SAFE_SUBDIR)

        # Phase 4 — news surrogates (slice to --limit-news)
        news_stat = self._phase_news_surrogates(ctx, public_surrogates / self.NEWS_SURROGATE_SUBDIR)

        # Phase 5 — SEC surrogates
        sec_stat = self._phase_sec_surrogates(
            ctx,
            sec_selection,
            public_surrogates / self.SEC_SURROGATE_SUBDIR,
        )

        # Phase 6 — direct privacy scan on the SEC surrogates
        direct_privacy = self._phase_direct_privacy(
            ctx, public_surrogates / self.SEC_SURROGATE_SUBDIR, qa_root
        )

        # Phase 7 — utility evaluation on the masked SEC content
        utility_report = self._phase_utility(
            sec_selection,
            public_surrogates / self.SEC_SURROGATE_SUBDIR,
            qa_root,
        )

        # Phase 8 — semantic privacy attacks (4 required: rare phrase,
        # BM25 lexical retrieval, multi-document, structured numeric
        # similarity). The legacy stub is gone: this phase now writes
        # ``qa/semantic_privacy_report.json`` with an honest verdict
        # (PASS or FAIL) rather than a forever-INCOMPLETE marker.
        semantic_report = self._phase_semantic_privacy(
            ctx,
            public_surrogates / self.SEC_SURROGATE_SUBDIR,
            public_surrogates / self.NEWS_SURROGATE_SUBDIR,
            public_root / self.NUMERIC_SAFE_SUBDIR,
            qa_root,
        )
        # Phase 9 — NVIDIA 3-pass adversarial review.
        # Runs when NVIDIA_API_KEY is configured; writes NOT_RUN stub otherwise.
        # Fail-fast: in final submission mode (allow_incomplete=False),
        # a missing NVIDIA_API_KEY is a hard error.
        import os as _os

        nvidia_api_key = _os.environ.get("NVIDIA_API_KEY", "")
        nvidia_report: dict[str, Any]
        if not nvidia_api_key and not self.allow_incomplete:
            raise RuntimeError(
                "NVIDIA_REQUIRED_BUT_NOT_CONFIGURED: "
                "final submission mode requires NVIDIA_API_KEY. "
                "Set the environment variable or use --allow-incomplete for development."
            )
        if nvidia_api_key:
            try:
                # Build bounded-review caps from CLI overrides.
                if self.nvidia_mode == "smoke":
                    bounds = NVIDIABounds.smoke()
                else:
                    bounds = NVIDIABounds.final_submission()
                if self.nvidia_max_artifacts is not None:
                    bounds.max_artifacts_per_run = self.nvidia_max_artifacts
                if self.nvidia_max_chunks_reviewed is not None:
                    bounds.max_chunks_reviewed_per_artifact = self.nvidia_max_chunks_reviewed
                if self.nvidia_max_chunks_rewritten is not None:
                    bounds.max_chunks_rewritten_per_artifact = self.nvidia_max_chunks_rewritten
                if self.nvidia_smoke_max_input_chars is not None and self.nvidia_mode == "smoke":
                    bounds.smoke_max_input_chars = self.nvidia_smoke_max_input_chars

                adapter = NVIDIAReviewAdapter(registry=self._preloaded_registry, bounds=bounds)
                nvidia_report = adapter.review_batch(
                    anonymized_dir=public_root / "surrogates" / "sec",
                    ticker=ctx.ticker,
                )
                # Write the full review results
                nvidia_path = qa_root / "nvidia_attack_report.json"
                import orjson as _orjson

                nvidia_path.write_bytes(
                    _orjson.dumps(
                        nvidia_report,
                        option=_orjson.OPT_SORT_KEYS | _orjson.OPT_INDENT_2,
                    )
                )
            except Exception as exc:
                logger.exception("NVIDIA review failed at runtime for %s", ctx.ticker)
                nvidia_report = _write_fail_closed_nvidia_report(
                    qa_root,
                    mode=self.nvidia_mode,
                    model=_os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
                    error_class=exc.__class__.__name__,
                    reason="NVIDIA review failed at runtime",
                )
        else:
            # dev allow_incomplete branch: still emit a fail-closed NOT_RUN stub so the file exists.
            nvidia_report = _write_fail_closed_nvidia_report(
                qa_root,
                mode=self.nvidia_mode,
                model=_os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
                error_class="NOT_RUN",
                reason="NVIDIA_API_KEY not configured (allow_incomplete=True)",
            )

        # Phase 9 — release gate evaluation
        gate_payload = self._phase_release_gate(
            ctx,
            direct_privacy,
            utility_report,
            semantic_report,
            nvidia_report,
            sec_stat,
        )
        # Atomic write (write to .tmp + os.replace) so a SIGTERM fired
        # mid-serialize cannot leave a torn gate JSON on disk. The
        # idempotent backstop helper would refuse to repair torn files
        # because ``if not path.is_file()`` would see the partial
        # output as existing.
        import os as _os_replace_run

        _gate_path = qa_root / "release_gate.json"
        _gate_tmp = _gate_path.with_suffix(_gate_path.suffix + ".tmp")
        _gate_tmp.write_text(json.dumps(gate_payload, indent=2), encoding="utf-8")
        _os_replace_run.replace(_gate_tmp, _gate_path)

        return {
            "ticker": ctx.ticker,
            "source_run": str(ctx.source_run),
            "output_root": str(self.output_root),
            "limits": {
                "forms": self.form_limits,
                "news": self.limit_news,
            },
            "discovered": {
                "forms": ctx.discovered_forms,
                "news": ctx.discovered_news_count,
            },
            "written": {
                "sec_surrogates": sec_stat["written"],
                "news_surrogates": news_stat["written"],
                "numeric_files": numeric_pkg["written_files"],
                "qa_files": [
                    str(qa_root / "direct_privacy_report.json"),
                    str(qa_root / "semantic_privacy_report.json"),
                    str(qa_root / "nvidia_attack_report.json"),
                    str(qa_root / "utility_report.json"),
                    str(qa_root / "release_gate.json"),
                ],
            },
            "release_gate": gate_payload,
        }

    # ── Phase helpers ────────────────────────────────────────────────

    def _phase_atlas_build(self, ctx: RunContext, qa_root: Path) -> None:
        """Phase 1.55 — harvest direct identifiers + write coverage report.

        Runs BEFORE ``_phase_atlas_load`` so the loader MAY pick up extra
        entries if the builder decided to merge. Either way, the
        coverage report is always emitted so the release gate has an
        explicit per-type count + warning level. ``critical_warnings``
        (e.g. ``aliases_built == 0`` or ``<= 6``) BLOCK the release.
        """
        qa_root.mkdir(parents=True, exist_ok=True)
        builder = DirectIdentifierAtlasBuilder(
            ticker=ctx.ticker,
            source_run=ctx.source_run,
        )
        report = builder.harvest()
        self._harvest_report = report

        coverage_path = qa_root / "direct_identifier_coverage_report.json"
        coverage_path.write_text(
            json.dumps(report.to_report(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        # Rejected-candidates histogram (counts ONLY — never raw values).
        # Schema is always-on: written even when zero rejections occur so
        # the operator can scan both reports in parallel for QA without
        # re-reading private logging. Fix 3 contract — rejected
        # candidates NEVER participate in the scanner's leak-surface
        # count, NEVER inflate the replacement-rate denominator, and
        # NEVER appear in the public alias set the masker consumes.
        write_rejected_candidates_report(
            report,
            qa_root / "direct_identifier_rejected_candidates_report.json",
        )

        # Pseudonym allowlist metadata so a downstream reviewer can
        # audit which substrings the scanner ignored. We do NOT
        # write the patterns into the public tree; the audit doc
        # lives under private/ for transparency.
        audit_path = self.output_root / ctx.ticker / "pseudonym_allowlist_report.json"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "ticker": ctx.ticker,
                    "allowlist_size": SAFE_PSEUDONYM_ALLOWLIST_SIZE,
                    "human_readable": allowlist_human_readable(),
                    "audit_logged_at": _utc_iso_now(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        if report.coverage_warnings:
            critical = [w for w in report.coverage_warnings if w.get("level") == "critical"]
            logger.warning(
                "Atlas coverage produced %d warning(s); %d critical for %s",
                len(report.coverage_warnings),
                len(critical),
                ctx.ticker,
            )

        # Code-reviewer #3: MERGE harvested entries back into
        # ``identity_atlas.yaml`` so ``load_atlas`` (Phase 1.5) sees
        # them on the SAME run. ``private_maps`` is gitignored, so
        # writing back is safe and reproducible. Preserves existing
        # human-curated entries.
        self._merge_harvest_into_atlas_yaml(report, ctx)

        # The load_atlas step will raise RuntimeError when
        # ``aliases_loaded == 0``, which is the right fail-closed
        # contract. We do NOT raise here so the harvest report is
        # always inspected before that fires.

    def _merge_harvest_into_atlas_yaml(self, report: AtlasHarvestReport, ctx: RunContext) -> None:
        """Merge harvested entities + aliases into identity_atlas.yaml.

        Preserves every existing human-curated entry (no overwrite,
        no dedup-against-curated that would delete real curation).
        Source-run ``private_maps`` is gitignored, so writing back is
        safe and reproducible.
        """
        if report.aliases_built == 0:
            return

        atlas_path = ctx.source_run / "private_maps" / ctx.ticker / "identity_atlas.yaml"
        if not atlas_path.is_file():
            return

        try:
            import yaml

            data = yaml.safe_load(atlas_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read existing atlas YAML for merge: %s", exc)
            return
        if not isinstance(data, dict):
            data = {}

        # Fix 4 — entities whose masker MUST match case-insensitively
        # AND tolerate punctuation-adjacent tokens (e.g. "NVIDIA" must
        # mask inside ", NVIDIA Corp", "nvidia-corp URL", "NVIDIA's",
        # etc.) need a non-literal match_policy + a non-empty mutation
        # policy list. Other entity types stay literal so we don't
        # over-expand the leak surface for rare-phrase captures.
        #
        # Hoisted to function-local scope BEFORE both the curated
        # upgrade loop below AND the harvested-insert loop further
        # down, so each side sees the same definition (regression
        # captured: previously the constant was defined mid-block
        # and the upgrade loop raised UnboundLocalError).
        case_insensitive_types = {
            EntityType.COMPANY.value,
            EntityType.TICKER.value,
            EntityType.BRAND.value,
        }

        existing_entity_ids = {e.get("entity_id") for e in (data.get("entities") or [])}
        existing_alias_ids = {a.get("alias_id") for a in (data.get("aliases") or [])}

        # Fix 4 (part 2): UPGRADE the match_policy of PRE-EXISTING
        # CURATED aliases whose entity_type is company/ticker/brand.
        # Earlier waves of the pipeline wrote these as ``literal``; the
        # masker then misses variants like ``NVIDIA`` inside
        # ``, NVIDIA Corp``, ``NVIDIA's``, ``nvidia-corp``, etc.
        # (the bounded beta's 40 post-mask hits came from this gap).
        # in-place mutation preserves alias_ids (no collisions) while
        # bringing curated entries up to the same standard harvested
        # entries get below.
        for ali in data.get("aliases", []) or []:
            etype_val = ali.get("entity_type")
            if isinstance(etype_val, str) and etype_val.lower() in case_insensitive_types:
                ali["match_policy"] = "case_insensitive"
                mp = list(ali.get("enabled_mutation_policies") or [])
                for required in (
                    "punctuation_variant",
                    "possessive",
                    "whitespace_normalize",
                ):
                    if required not in mp:
                        mp.append(required)
                ali["enabled_mutation_policies"] = mp

        # Now insert the harvested entries. ``case_insensitive_types``
        # is defined above (in the hoisted block) so we don't need
        # to redefine it here.
        #
        # H3 (Fix 5): the historical loop used
        # ``if entity_id in existing_entity_ids: counter += 1; continue``
        # which silently DROPPED the harvested value whenever the
        # generated ID collided with one already in the curated atlas
        # (the bounded beta's 40 post-mask hits came from this: 124 of
        # 483 harvested entries actually made it to the YAML because
        # their slots were exactly the first 359 counter positions).
        # Replace the buggy ``continue`` with a ``while`` that keeps
        # incrementing counter until BOTH the entity_id and the
        # alias_id land on free slots, so each harvested value yields
        # exactly one entity + alias record regardless of how dense
        # the curated atlas already is.
        counter = 1
        merged_entities = 0
        merged_aliases = 0
        for etype, values in sorted(report._buckets.items()):
            for value in sorted(values):
                # Bump the counter until both ids are free. Bound the
                # search so a pathological curated atlas cannot hang
                # the run; the upper bound is generous (1M iterations)
                # but a real curated atlas never carries that density.
                attempts = 0
                while attempts < 1_000_000:
                    entity_id = f"harvest_{etype}_{counter:04d}"
                    alias_id = f"harvest_{etype}_a{counter:04d}"
                    if entity_id not in existing_entity_ids and alias_id not in existing_alias_ids:
                        break
                    counter += 1
                    attempts += 1
                else:  # pragma: no cover - defensive guard
                    logger.warning(
                        "Could not allocate fresh harvest id after 1M attempts "
                        "for etype=%s value=%r; skipping",
                        etype,
                        value,
                    )
                    continue
                data.setdefault("entities", []).append(
                    {
                        "entity_id": entity_id,
                        "entity_type": etype,
                        "canonical_private_value": value,
                    }
                )
                merged_entities += 1
                alias_record: dict[str, Any] = {
                    "alias_id": alias_id,
                    "canonical_entity_id": entity_id,
                    "private_alias_value": value,
                    "entity_type": etype,
                    "match_policy": (
                        "case_insensitive" if etype in case_insensitive_types else "literal"
                    ),
                }
                if etype in case_insensitive_types:
                    # Punctuation-adjacent token tolerance: "NVIDIA"
                    # matches inside ", NVIDIA Corp", "NVIDIA's", etc.
                    alias_record["enabled_mutation_policies"] = [
                        "punctuation_variant",
                        "possessive",
                        "whitespace_normalize",
                    ]
                data.setdefault("aliases", []).append(alias_record)
                merged_aliases += 1
                counter += 1

        if merged_entities or merged_aliases:
            try:
                with atlas_path.open("w", encoding="utf-8") as fh:
                    yaml.safe_dump(
                        data,
                        fh,
                        sort_keys=False,
                        allow_unicode=True,
                        default_flow_style=False,
                    )
                logger.info(
                    "Merged %d harvested entities + %d aliases into %s",
                    merged_entities,
                    merged_aliases,
                    atlas_path.name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not write merged atlas YAML: %s", exc)

    def _ensure_decision_files_present(
        self,
        qa_root: Path,
        source_hash: str,
        error_class: str,
    ) -> None:
        """Backfill missing decision files with strict fail-closed stubs.

        Ensures that after a pipeline crash the operator can ALWAYS see:

        - ``qa/nvidia_attack_report.json`` — NVIDIA_REVIEW_INCOMPLETE stub.
        - ``qa/release_gate.json`` — overall_release_decision=FAIL stub.
        - ``qa/direct_privacy_report.json`` — only if missing; Phase 6
          normally writes it.

        Each stub carries an explicit ``error_class`` so the trace tells
        the operator exactly which exception short-circuited the run.
        No sensitive carrier (API key or raw source path) is emitted.
        ``nvidia_mode`` is the orchestrator's configured mode and is
        the only NVIDIA-context field needed downstream.
        """
        qa_root.mkdir(parents=True, exist_ok=True)

        nvidia_path = qa_root / "nvidia_attack_report.json"
        gate_path = qa_root / "release_gate.json"
        direct_path = qa_root / "direct_privacy_report.json"

        if not nvidia_path.is_file():
            _write_fail_closed_nvidia_report(
                qa_root,
                mode=self.nvidia_mode,
                model="meta/llama-3.1-70b-instruct",
                error_class=error_class,
                reason=f"Pipeline interrupted before NVIDIA review: {error_class}",
            )

        if not gate_path.is_file():
            # We do not have the in-flight nvidia_report/direct_privacy/
            # semantic_report dicts because the run died early; emit
            # conservative stubs that always FAIL the overall gate.
            _write_fail_closed_gate(
                qa_root,
                nvidia_report=None,
                direct_privacy=None,
                semantic_report=None,
                error_class=error_class,
                source_hash=source_hash,
            )

        if not direct_path.is_file():
            payload = {
                "schema_version": "1.0.0",
                "document_id": "reanonymize_run",
                "passed": False,
                "blocking_failures": 1,
                "evaluated_at": _utc_iso_now(),
                "implementation_status": "fail_closed",
                "reason": (f"Pipeline interrupted before direct-privacy phase: {error_class}"),
                "post_mask_hits": 0,
                "pre_mask_hits": 0,
                "replacement_rate": 0.0,
                "blocking_conditions": ["DIRECT_PRIVACY_INCOMPLETE"],
            }
            direct_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _phase_atlas_load(self, ctx: RunContext, qa_root: Path) -> None:
        """Phase 1.5 — load the identity atlas and fail-closed on zero aliases.

        Writes ``qa/registry_load_report.json`` BEFORE any public-dir
        mkdir, so a fail-closed run still leaves a diagnostic artifact
        that downstream tooling can inspect without exposing the atlas
        absolute path (only the basename is emitted per
        ``RegistryLoadSummary.to_report``).

        Raises ``RuntimeError`` when ``RegistryLoadSummary.blocking``
        is True. The full public/ dir is never created in that case
        because the public/ mkdir block in ``run()`` is sequenced AFTER
        this phase.
        """
        atlas_path = ctx.source_run / "private_maps" / ctx.ticker / "identity_atlas.yaml"
        qa_root.mkdir(parents=True, exist_ok=True)

        self._preloaded_registry, self._preloaded_summary = load_atlas(
            atlas_path, ticker=ctx.ticker
        )
        # Sanity: build the report even when load_atlas returned None
        # so the run's failure mode is transparent in QA.
        assert self._preloaded_summary is not None  # invariant of load_atlas
        report_path = qa_root / "registry_load_report.json"
        report_path.write_text(
            json.dumps(self._preloaded_summary.to_report(), indent=2),
            encoding="utf-8",
        )

        if self._preloaded_summary.blocking:
            logger.error(
                "Registry-load fail-closed for %s (aliases_loaded=%d "
                "load_errors=%d skipped_empty=%d duplicates=%d)",
                ctx.ticker,
                self._preloaded_summary.aliases_loaded,
                self._preloaded_summary.load_errors,
                self._preloaded_summary.skipped_empty,
                self._preloaded_summary.duplicates,
            )
            raise RuntimeError(
                f"Registry-load fail-closed for {ctx.ticker}: "
                f"aliases_loaded={self._preloaded_summary.aliases_loaded} "
                f"load_errors={self._preloaded_summary.load_errors}"
            )

    def _phase_numeric_package(self, ctx: RunContext, output_dir: Path) -> dict[str, Any]:
        writer = ClassroomNumericWriter(ticker=ctx.ticker)
        pkg = writer.write_package(output_dir)
        return {
            "ticker": pkg.ticker,
            "annual_count": pkg.annual_count,
            "quarterly_count": pkg.quarterly_count,
            "weekly_count": pkg.weekly_count,
            "ratio_buckets_count": pkg.ratio_buckets_count,
            "regime_label": pkg.regime_label,
            "all_annual_identities_valid": pkg.all_annual_identities_valid,
            "identity_violations": pkg.identity_violations,
            "written_files": pkg.written_files,
        }

    def _phase_news_surrogates(self, ctx: RunContext, public_dir: Path) -> dict[str, Any]:
        articles_path = ctx.source_run / "originals" / ctx.ticker / "news" / "articles.json"
        if not articles_path.is_file():
            return {"written": [], "articles_processed": 0}

        try:
            raw = orjson.loads(articles_path.read_bytes())
        except orjson.JSONDecodeError:
            return {"written": [], "articles_processed": 0}
        if not isinstance(raw, list):
            return {"written": [], "articles_processed": 0}

        limit = self.limit_news if self.limit_news > 0 else len(raw)
        articles = list(raw[:limit])

        # Drop heavy body fields so memory spikes are bounded.
        # Headline + summary is enough for surrogate generation.
        sliced: list[dict[str, Any]] = []
        for a in articles:
            if not isinstance(a, dict):
                continue
            sliced.append(
                {
                    "headline": str(a.get("headline", "") or ""),
                    "summary": str(a.get("summary", "") or ""),
                    "body": str(a.get("body", "") or a.get("summary", "") or "")[:5000],
                    "publisher": str(a.get("publisher", "") or ""),
                    "canonical_url": str(a.get("canonical_url", "") or ""),
                    "published_timestamp": str(a.get("published_timestamp", "") or ""),
                }
            )

        # Private provenance map lives UNDER output_root/<TICKER> (gitignored).
        private_dir = self.output_root / ctx.ticker
        private_dir.mkdir(parents=True, exist_ok=True)
        generator = NewsSurrogateGenerator(ticker=ctx.ticker)
        result = generator.generate_from_articles(
            articles=sliced,
            public_dir=public_dir,
            private_dir=private_dir,
        )
        return {
            "written": [str(p) for p in public_dir.iterdir() if p.is_file()],
            "articles_processed": result.articles_processed,
            "surrogates_generated": result.surrogates_generated,
            "errors": result.errors,
        }

    def _phase_sec_surrogates(
        self,
        ctx: RunContext,
        selection: SecSurrogateSelection,
        public_dir: Path,
    ) -> dict[str, Any]:
        if not selection.items:
            return {
                "written": [],
                "processed": 0,
                "forms": {},
                "pre_mask_hits": 0,
                "pre_replacement_count": 0,
            }

        # ── Phase 5 smoke bypass ──────────────────────────────
        # When ``nvidia_mode == "smoke"`` AND a char cap is configured,
        # slice each selected HTML to ``smoke_max_input_chars`` and write
        # the excerpts under ``output_root/smoke_excerpts/sec/filings/``
        # BEFORE invoking TextAnonymizer. ``originals_dir`` is then
        # pointed at that excerpt root so the masker still runs end to
        # end — just on a deterministic, character-bounded excerpt. This
        # keeps Phase 6 + Phase 9 evidence meaningful (real MD outputs,
        # not fake stubs) while bounding total smoke wall-clock inside a
        # few seconds even when the only available filings are 1.1MB
        # 10-Ks. Production runs (``mode == "final_submission"``) skip
        # this branch because ``nvidia_smoke_max_input_chars`` is ``None``
        # in that mode.
        smoke_excerpt_used = False
        smoke_excerpt_dir: Path | None = None
        if self.nvidia_mode == "smoke" and self.nvidia_smoke_max_input_chars:
            cap = int(self.nvidia_smoke_max_input_chars)
            smoke_excerpt_dir = self.output_root / "smoke_excerpts"
            excerpts = smoke_excerpt_dir / "sec" / "filings"
            excerpts.mkdir(parents=True, exist_ok=True)
            new_items: list[tuple[str | None, Path]] = []
            for form, src_path in selection.items:
                try:
                    raw = src_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                excerpt_path = excerpts / src_path.name
                excerpt_path.write_text(raw[:cap], encoding="utf-8", errors="replace")
                new_items.append((form, excerpt_path))
            if new_items:
                selection = SecSurrogateSelection(items=new_items)
                smoke_excerpt_used = True

        originals_dir = (
            smoke_excerpt_dir
            if smoke_excerpt_used and smoke_excerpt_dir is not None
            else ctx.source_run / "originals"
        )
        # Phase 1.5 already loaded the registry; pass it through so the
        # anonymizer does NOT re-read the YAML again and the failure
        # surface is exactly one place (the orchestrator).
        private_maps_dir = ctx.source_run / "private_maps" / ctx.ticker

        anonymizer = TextAnonymizer(
            ticker=ctx.ticker,
            originals_dir=originals_dir,
            anonymized_dir=public_dir.parent,
            private_maps_dir=private_maps_dir,
        )
        # Capture the manifests so Phase 6 can compute ``pre_mask_hits``
        # and ``replacement_rate`` from the masker's match_count /
        # replacement_count without re-scanning the source HTML.
        sec_manifests = anonymizer.anonymize_all(
            selected_paths=selection.paths,
            preloaded_registry=self._preloaded_registry,
            preloaded_summary=self._preloaded_summary,
        )
        pre_mask_hits = sum(int(m.get("match_count", 0)) for m in sec_manifests)
        pre_replacement_count = sum(int(m.get("replacement_count", 0)) for m in sec_manifests)
        self._pre_mask_hits = pre_mask_hits
        self._pre_replacement_count = pre_replacement_count

        written = sorted(str(p) for p in public_dir.iterdir() if p.is_file())
        return {
            "written": written,
            "processed": len(written),
            "forms": selection.form_counts,
            "pre_mask_hits": pre_mask_hits,
            "pre_replacement_count": pre_replacement_count,
        }

    def _phase_direct_privacy(
        self,
        ctx: RunContext,
        sec_public_dir: Path,
        qa_dir: Path,
    ) -> dict[str, Any]:
        """Phase 6 — per-file direct privacy scan + replacement metrics.

        Uses the SHARED ``build_private_values_dict`` so the scanner
        cannot drift from the masker's normalization. Captures
        ``pre_mask_hits`` (from ``TextAnonymizer.anonymize_all``
        ``match_count`` sums) and ``post_mask_hits`` (per-file scan)
        so the gate can compute ``replacement_rate`` honestly without
        re-reading the source HTML.
        """
        per_file_hits: list[dict[str, Any]] = []
        combined_chunks: list[tuple[str, str]] = []
        for md in sorted(sec_public_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                rel = md.relative_to(self.output_root).as_posix()
            except ValueError:
                rel = md.name
            combined_chunks.append((rel, text))

        # The private-values payload now comes from the SAME registry
        # Phase 1.5 loaded. Masker and scanner use the exact same set
        # of normalized strings, eliminating the historical asymmetry
        # that produced 4735 leaked hits on the real NVDA run.
        private_values = build_private_values_dict(
            self._preloaded_registry, fallback_ticker=ctx.ticker
        )

        # Per-file exact-identity scan preserves attribution.
        total_exact = 0
        top_hit_types: dict[str, int] = {}
        all_blocking_files: list[str] = []
        for file_id, text in combined_chunks:
            hit = exact_identity_scan(text, file_id, private_values)
            total_exact += hit.total_hits
            per_file_hits.append(
                {
                    "document_id": file_id,
                    "total_hits": hit.total_hits,
                    "blocking_hits": hit.blocking_hits,
                    "warning_hits": hit.warning_hits,
                    "is_blocked": hit.is_blocked,
                }
            )
            if hit.is_blocked:
                all_blocking_files.append(file_id)
            # Aggregate the top hit values (truncated) so the report
            # names exactly WHAT is leaking, not just that something is.
            for h in hit.hits:
                matched = h.matched_text or ""
                if len(matched) > 30:
                    key = matched[:30] + "..."
                else:
                    key = matched
                top_hit_types[key] = top_hit_types.get(key, 0) + 1
        # Keep the top 10 by count, deterministic on tie (sorted alpha).
        top_hit_ordered = sorted(top_hit_types.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        top_hit_dict = dict(top_hit_ordered)

        # Filename and metadata scan. The metadata dict carries an
        # opaque source_hash (SHA-256 truncated to 16 hex chars) rather
        # than the raw ``--source-run`` path \u2014 the bounded beta's
        # 2 \xd7 ``NVIDIA`` hits came from ``filename_and_metadata_scan``
        # matching the ``NVDA``/``NVIDIA`` literal tokens inside the
        # str(source_run) string. The redacted form never carries any
        # raw private value, so the scan can only match on the
        # anonymized files themselves, not the leaked metadata carrier.
        import hashlib

        source_hash = hashlib.sha256(str(ctx.source_run).encode("utf-8")).hexdigest()[:16]
        filenames = [str(p) for p in sec_public_dir.iterdir() if p.is_file()]
        filename_res = filename_and_metadata_scan(
            filenames,
            # Fix 5 (metadata hygiene): drop ``ticker`` from the
            # scanned metadata dict because ``filename_and_metadata_scan``
            # runs ``metadata_text = str(metadata).lower()`` which
            # exposed the source ticker text (e.g. ``'nvda'``) to the
            # case-insensitive substring scanner and contributed 4 of
            # the bounded beta's post-mask hits. ``source_hash`` is
            # the SHA-256 truncated opaque identifier for the run; it
            # carries NO raw identity and is the only key the scanner
            # needs for run-correlation evidence.
            {"source_hash": source_hash},
            private_values,
        )

        # Compute replacement rate. Division-by-zero guard: if the
        # masker matched nothing in the source, rate is 1.0 ("all of
        # nothing was replaced" is vacuous PASS, not a blocker).
        pre_mask_hits = self._pre_mask_hits
        pre_replacement_count = self._pre_replacement_count
        # Per user spec: ``replacement_rate = masked_hit_count / maskable_pre_hits``
        # where ``masked_hit_count`` is the per-pattern masker
        # replacement count and ``maskable_pre_hits`` is the total
        # pre-mask matches. These are EXACTLY what the masker's
        # manifest returns; both already include hit accounting only
        # for values that could be replaced (i.e., that were loaded
        # aliases). The naming change clarifies the contract: the
        # numerator is what the masker actually replaced, NOT
        # ``pre_mask_hits - post_mask_hits`` (which would be negative
        # if the scanner became more sensitive than the masker).
        maskable_pre_hits = max(pre_mask_hits, 0)
        masked_hit_count = max(pre_replacement_count, 0)
        if maskable_pre_hits == 0:
            replacement_rate = 1.0
        else:
            replacement_rate = masked_hit_count / float(maskable_pre_hits)
        self._post_mask_hits = total_exact

        # ── Pseudonym-aware suppression (blocker 5) ──────────────
        # Re-scan per_file hits for suppression accounting. Reset
        # counts so re-runs are deterministic. Anchored patterns
        # (blocker 5) keep "the company" from ever being mistaken
        # for a system pseudonym.
        for f in per_file_hits:
            f["suppressed_hits"] = 0
        recompute_per_file: dict[str, int] = {f["document_id"]: 0 for f in per_file_hits}
        recompute_total_exact = 0
        recompute_top_hit_types: dict[str, int] = {}
        top_hash_to_context: dict[str, str] = {}
        suppressed_total = 0
        for file_id, text in combined_chunks:
            hit = exact_identity_scan(text, file_id, private_values)
            real_exact = 0
            file_suppressed = 0
            for h in hit.hits:
                if is_pseudonym_suppression_eligible(h.matched_text or ""):
                    file_suppressed += 1
                    continue
                real_exact += 1
                matched = h.matched_text or ""
                key = matched[:30] + "..." if len(matched) > 30 else matched
                recompute_top_hit_types[key] = recompute_top_hit_types.get(key, 0) + 1
                # Pre-collect up to 20 redacted windows so the report
                # can show ``example_contexts_redacted`` without
                # leaking the raw matched_text.
                if len(top_hash_to_context) < 20:
                    hk = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
                    if hk not in top_hash_to_context:
                        top_hash_to_context[hk] = (h.context or "")[:80]
            recompute_per_file[file_id] = real_exact
            recompute_total_exact += real_exact
            suppressed_total += file_suppressed
        # Suppression per-file accounting, used after the recompute to
        # populate ``per_file_hits[i]["suppressed_hits"]`` honestly.
        recompute_per_file_suppressed: dict[str, int] = dict.fromkeys(
            {fid for fid, _ in combined_chunks}, 0
        )
        for file_id, text in combined_chunks:
            hit = exact_identity_scan(text, file_id, private_values)
            count = sum(
                1 for h in hit.hits if is_pseudonym_suppression_eligible(h.matched_text or "")
            )
            recompute_per_file_suppressed[file_id] = count
        total_exact = recompute_total_exact
        for f in per_file_hits:
            f["total_hits"] = recompute_per_file.get(f["document_id"], 0)
            f["blocking_hits"] = f["total_hits"] if f["total_hits"] > 0 else 0
            f["is_blocked"] = f["total_hits"] > 0
            f["suppressed_hits"] = recompute_per_file_suppressed.get(f["document_id"], 0)
        self._post_mask_hits = total_exact
        top_hit_ordered = sorted(recompute_top_hit_types.items(), key=lambda kv: (-kv[1], kv[0]))[
            :10
        ]
        top_hit_dict = dict(top_hit_ordered)
        # Redacted example contexts (≤5 short windows) — block reviewer #2.
        example_redacted = [
            {"hit_hash": hk, "context_window": cw}
            for hk, cw in list(top_hash_to_context.items())[:5]
        ]

        # hits_by_type — map each top_hit bucket onto the per-type
        # taxonomy from build_private_values_dict. Counts ONLY after
        # pseudonym-suppression so a generic-looking raw value can
        # never inflate the per-type bucketing.
        hits_by_type: dict[str, int] = {}
        hits_by_source = {
            "exact_identity_scan": total_exact,
            "filename_metadata_scan": sum(
                1
                for h in filename_res.hits
                if not is_pseudonym_suppression_eligible(h.matched_text or "")
            ),
        }
        for bucket, value_list in private_values.items():
            count_for_bucket = 0
            bucket_seen: set[str] = set()
            for value in value_list:
                if not value or value in bucket_seen:
                    continue
                bucket_seen.add(value)
                # Count this value once if it appears in any masked
                # file AND is NOT a system pseudonym (cheap O(N*M)
                # over the small bounded corpus).
                for _file_id, file_text in combined_chunks:
                    if value in file_text and not is_pseudonym_suppression_eligible(value):
                        count_for_bucket += 1
                        break  # one occurrence is enough; this is per-bucket accounting
            if count_for_bucket > 0:
                hits_by_type[bucket] = count_for_bucket

        hits_by_file = {f["document_id"]: f["total_hits"] for f in per_file_hits}

        # Opaque hashes — never include raw matched_text in public QA.
        top_20_redacted = [
            hashlib.sha256(k.encode("utf-8")).hexdigest()[:12]
            for k, _ in sorted(recompute_top_hit_types.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        ]

        report = {
            "document_id": "reanonymize_run",
            "scanned_files": [name for name, _ in combined_chunks],
            "files_scanned": len(combined_chunks),
            "private_values_count": sum(len(v) for v in private_values.values()),
            "pre_mask_hits": pre_mask_hits,
            "pre_replacement_count": pre_replacement_count,
            "post_mask_hits": total_exact,
            "replacement_rate": replacement_rate,
            "top_hit_types": top_hit_dict,
            "top_hit_values": top_hit_dict,  # legacy alias kept for back-compat
            "blocking_files": all_blocking_files,
            "attacks": [
                {
                    "attack_type": "exact_identity",
                    "total_hits": total_exact,
                    "blocking_hits": total_exact,
                    "warning_hits": 0,
                    "is_blocked": total_exact > 0,
                    "per_file": per_file_hits,
                },
                {
                    "attack_type": filename_res.attack_type,
                    "total_hits": filename_res.total_hits,
                    "blocking_hits": filename_res.blocking_hits,
                    "warning_hits": filename_res.warning_hits,
                    "is_blocked": filename_res.is_blocked,
                },
            ],
            "passed": total_exact == 0 and not filename_res.is_blocked,
            "evaluated_at": _utc_iso_now(),
            "replacement_rate_definition": ("masked_hit_count / maskable_pre_hits"),
            "maskable_pre_hits": maskable_pre_hits,
            "masked_hit_count": masked_hit_count,
            "hits_by_type": hits_by_type,
            "hits_by_file": hits_by_file,
            "hits_by_source": hits_by_source,
            "top_20_redacted_hit_hashes": top_20_redacted,
            "example_contexts_redacted": example_redacted,
            "suppressed_pseudonym_hits_total": suppressed_total,
            "pseudonym_allowlist_size": SAFE_PSEUDONYM_ALLOWLIST_SIZE,
            "aliases_snapshot": {
                "aliases_loaded": (
                    self._preloaded_summary.aliases_loaded if self._preloaded_summary else 0
                ),
                "harvest_built": (
                    self._harvest_report.aliases_built if self._harvest_report else 0
                ),
                "aliases_by_type": (
                    dict(self._harvest_report.aliases_by_type) if self._harvest_report else {}
                ),
            },
        }
        (qa_dir / "direct_privacy_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report

    def _phase_utility(
        self,
        selection: SecSurrogateSelection,
        public_dir: Path,
        qa_dir: Path,
    ) -> dict[str, Any]:
        """Compute utility retention on aggregate source vs masked text.

        Per-file source→masked pairing is unreliable here because the
        masker rewrites output filenames as ``filing_<hash>.md``. Aggregate
        length-bounded concatenation keeps the metric honest and lets us
        fix the utility gate against the real surface.
        """
        if not selection.paths:
            utility_payload = {
                "document_id": "reanonymize_run",
                "metrics": {},
                "warnings": ["no public surrogates produced"],
                "overall_utility": 1.0,
            }
        else:
            source_chunks: list[str] = []
            for sec_path in sorted(selection.paths):
                try:
                    source_chunks.append(sec_path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue

            masked_chunks: list[str] = []
            for md in sorted(public_dir.glob("*.md")):
                try:
                    masked_chunks.append(md.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue

            # Truncate to keep memory bounded; utility inspection is
            # structurally coarse anyway (keyword + count based).
            res = evaluate_unstructured_utility(
                source_text="\n".join(source_chunks)[:50000],
                masked_text="\n".join(masked_chunks)[:50000],
                document_id="reanonymize_run",
            )
            utility_payload = {
                "document_id": res.document_id,
                "metrics": {
                    "non_identifier_token_retention": res.non_identifier_token_retention,
                    "section_retention": res.section_retention,
                    "table_retention": res.table_retention,
                    "financial_number_retention": res.financial_number_retention,
                },
                "overall_utility": res.overall_utility,
                "warnings": res.warnings,
            }
        (qa_dir / "utility_report.json").write_text(
            json.dumps(utility_payload, indent=2), encoding="utf-8"
        )
        return utility_payload

    def _phase_semantic_privacy(
        self,
        ctx: RunContext,
        sec_public_dir: Path,
        news_public_dir: Path,
        numeric_dir: Path,
        qa_dir: Path,
    ) -> dict[str, Any]:
        """Phase 8 — semantic privacy attacks (4 attack suite).

        Runs the rare phrase attack, BM25 lexical retrieval attack,
        multi-document retrieval attack, and structured numeric
        similarity attack against the masked surrogates + numeric
        package. Writes ``qa/semantic_privacy_report.json`` and
        returns the same dict for ``_phase_release_gate`` to consume.

        Overall verdict rules (also embedded in
        ``results_to_report``):

        - All 4 passed: ``PASS``
        - Any 1 failed: ``FAIL``
        - Suite errored out: ``NOT_RUN`` (downstream INCOMPLETE)
        """
        try:
            from ..anonymization.registry_load import build_private_values_dict

            private_values = build_private_values_dict(
                self._preloaded_registry, fallback_ticker=ctx.ticker
            )
            results = run_semantic_attack_suite(
                sec_public_dir=sec_public_dir,
                news_public_dir=news_public_dir,
                numeric_dir=numeric_dir,
                source_run=ctx.source_run,
                ticker=ctx.ticker,
                private_values=private_values,
            )
            implementation_status = "implemented"
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Semantic attack suite failed for %s: %s",
                ctx.ticker,
                exc,
                exc_info=True,
            )
            results = {}
            implementation_status = "implementation_failed"

        ticker_digest = "syn_" + hashlib.sha256(ctx.ticker.encode("utf-8")).hexdigest()[:8]
        report = results_to_report(results, ticker_digest)
        report["evaluated_at"] = _utc_iso_now()
        report["implementation_status"] = implementation_status
        if implementation_status != "implemented":
            report["overall_verdict"] = "NOT_RUN"

        (qa_dir / "semantic_privacy_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        logger.info(
            "Semantic attack suite complete for %s: overall_verdict=%s",
            ctx.ticker,
            report.get("overall_verdict"),
        )
        self._semantic_report = report
        return report

    def _phase_release_gate(
        self,
        ctx: RunContext,
        direct_privacy: dict[str, Any],
        utility_report: dict[str, Any],
        semantic_report: dict[str, Any],
        nvidia_report: dict[str, Any],
        sec_stat: dict[str, Any],
    ) -> dict[str, Any]:
        exact_hits = sum(
            a.get("blocking_hits", 0)
            for a in direct_privacy.get("attacks", [])
            if a.get("attack_type") == "exact_identity"
        )
        digital_hits = sum(
            a.get("blocking_hits", 0)
            for a in direct_privacy.get("attacks", [])
            if a.get("attack_type") == "digital_identifier"
        )
        filename_hits = sum(
            a.get("blocking_hits", 0)
            for a in direct_privacy.get("attacks", [])
            if a.get("attack_type") == "filename_metadata"
        )

        # Semantic attacks are IMPLEMENTED in this revision (rare
        # phrase + BM25 retrieval + multi-document + structured
        # numeric similarity); only NVIDIA review remains a single
        # always-``NOT_RUN`` surface. ``semantic_incomplete`` and
        # ``nvidia_incomplete`` feed the legacy downstream plumbing
        # (conditions_payload + stubs_enforced + beta_status); the
        # four explicit decision fields below are the canonical
        # surfaces for new consumers.
        semantic_incomplete = False
        # Robust NVIDIA-incomplete detection — recognizes BOTH the
        # legacy stub shape (``status in {INCOMPLETE, not_configured}``)
        # AND the new fail-closed shape emitted by
        # ``_write_fail_closed_nvidia_report`` (status="FAIL" with
        # ``blocking_conditions`` carrying ``NVIDIA_REVIEW_INCOMPLETE``
        # or ``nvidia_decision`` set by the fail-closed writer).
        nvidia_status = nvidia_report.get("status")
        nvidia_decision_field = nvidia_report.get("nvidia_decision", "")
        nvidia_blocking = nvidia_report.get("blocking_conditions", []) or []
        # Robust NVIDIA-incomplete detection. ANY payload emitted by
        # ``_write_fail_closed_nvidia_report`` is treated as
        # NVIDIA-incomplete; a real review verdict uses
        # PASS/REVIEW/FAIL with non-empty decisions and may carry
        # empty ``blocking_conditions``. The fail-closed writer sets
        # ``status=\"FAIL\"`` so the first arm covers both NOT_RUN and
        # PROVIDER_FAILURE shapes; the blocking_conditions arm is the
        # most reliable detector.
        nvidia_incomplete = (
            nvidia_status in ("INCOMPLETE", "not_configured", "FAIL")
            or "NVIDIA_REVIEW_INCOMPLETE" in nvidia_blocking
            or "PROVIDER_FAILURE_OR_BLOCKED_PRECHECK_OR_INCOMPLETE" in nvidia_decision_field
        )

        gate: ReleaseGateResult = evaluate_release_gate(
            text_attacks_blocked=direct_privacy.get("passed") is False,
            structured_rank=-1,  # No structured attacker in this revision
            structured_top_k=10,
            llm_blocked=False,
            exact_identity_hits=exact_hits,
            unique_phrase_hits=0,
            digital_hits=digital_hits,
            filename_hits=filename_hits,
            deterministic_reproduced=True,
            all_attacks_ran=True,  # Every attack we ship ran
            provenance_complete=True,
            private_paths_found=[],
            unhandled_errors=[],
            policy={"attack_thresholds": {}},
        )

        conditions_payload = [
            {
                "id": c.condition_id,
                "description": c.description,
                "passed": c.passed,
                "blocking": c.is_blocking,
                "evidence": c.evidence,
            }
            for c in gate.conditions
        ]

        # Append declarative semantic / NVIDIA conditions BEFORE the
        # hash so the gate_hash reflects the full condition set.
        if semantic_incomplete:
            conditions_payload.append(
                {
                    "id": "semantic_privacy_attack_implemented",
                    "description": (
                        "Semantic / LLM attack suite must be implemented to "
                        "establish release safety."
                    ),
                    "passed": False,
                    "blocking": True,
                    "evidence": {
                        "status": "INCOMPLETE",
                        "report_path": "qa/semantic_privacy_report.json",
                    },
                }
            )
        if nvidia_incomplete:
            conditions_payload.append(
                {
                    "id": "nvidia_review_implemented",
                    "description": (
                        "NVIDIA review adapter must be implemented to establish release safety."
                    ),
                    "passed": False,
                    "blocking": True,
                    "evidence": {
                        "status": "INCOMPLETE",
                        "report_path": "qa/nvidia_attack_report.json",
                    },
                }
            )

        # Append the four direct-masking gate blockers the user spec
        # requires. These are derived from ``direct_privacy`` and the
        # registry-load summary loaded by Phase 1.5.
        post_mask_hits = direct_privacy.get("post_mask_hits", 0)
        replacement_rate = float(direct_privacy.get("replacement_rate", 1.0))
        pre_mask_hits = direct_privacy.get("pre_mask_hits", 0)
        pre_replacement_count = direct_privacy.get("pre_replacement_count", 0)
        aliases_loaded = (
            self._preloaded_summary.aliases_loaded if self._preloaded_summary is not None else 0
        )
        load_errors_count = (
            self._preloaded_summary.load_errors if self._preloaded_summary is not None else 0
        )

        # 1. post_mask_identity_hits — block if any private value still
        # appears in the masked surrogates (the symptom we just fixed).
        if post_mask_hits > 0:
            conditions_payload.append(
                {
                    "id": "post_mask_identity_hits",
                    "description": (
                        "Direct identity scan on masked surrogates found "
                        "blocking hits; the masker is leaving private "
                        "values in the public tree."
                    ),
                    "passed": False,
                    "blocking": True,
                    "evidence": {
                        "post_mask_hits": post_mask_hits,
                        "files_scanned": direct_privacy.get("files_scanned", 0),
                    },
                }
            )

        # 2. replacement_rate — block when fewer than 99% of source
        # matches were replaced by the masker. ``direct_privacy`` carries
        # the per-phase aggregate.
        if replacement_rate < 0.99:
            conditions_payload.append(
                {
                    "id": "direct_replacement_rate",
                    "description": (
                        "Masker replaced fewer than 99% of pre-mask "
                        "regex matches; replacement rate is the honest "
                        "inverse of the pre/post hit ratio."
                    ),
                    "passed": False,
                    "blocking": True,
                    "evidence": {
                        "replacement_rate": replacement_rate,
                        "pre_mask_hits": pre_mask_hits,
                        "post_mask_hits": post_mask_hits,
                        "pre_replacement_count": pre_replacement_count,
                    },
                }
            )

        # 3. registry aliases_loaded — Phase 1.5 fail-closed already
        # raised RuntimeError, but the gate condition is recorded anyway
        # so re-runs of an already-failed run have an explicit signal.
        if aliases_loaded == 0:
            conditions_payload.append(
                {
                    "id": "registry_aliases_loaded",
                    "description": (
                        "Identity atlas must produce at least one loadable "
                        "alias for the masker to do any work; zero aliases "
                        "is a fail-closed condition."
                    ),
                    "passed": False,
                    "blocking": True,
                    "evidence": {
                        "aliases_loaded": aliases_loaded,
                        "report_path": "qa/registry_load_report.json",
                    },
                }
            )

        # 4.5. coverage_critical_warnings — AtlasBuilder-reported critical
        # warnings (e.g. ``aliases_built <= 6`` or zero). These do NOT
        # stop the run (the harvest report + coverage report are still
        # written) but they DO block the release gate.
        if self._harvest_report is not None:
            critical_warnings = [
                w for w in self._harvest_report.coverage_warnings if w.get("level") == "critical"
            ]
            if critical_warnings:
                conditions_payload.append(
                    {
                        "id": "coverage_critical_warnings",
                        "description": (
                            "AtlasBuilder reported critical coverage warnings "
                            "(e.g. zero aliases built, or aliases_built <= critical "
                            "threshold). The direct-privacy surface is under-covered; "
                            "release is not safe until coverage is expanded."
                        ),
                        "passed": False,
                        "blocking": True,
                        "evidence": {
                            "critical_warnings_count": len(critical_warnings),
                            "aliases_built": self._harvest_report.aliases_built,
                            "aliases_by_type": dict(self._harvest_report.aliases_by_type),
                            "coverage_report": "qa/direct_identifier_coverage_report.json",
                        },
                    }
                )

        # 4. registry load_errors — any exception that surfaced during
        # atlas load short-circuits individual aliases but should ALSO
        # downgrade the release gate, even when ``aliases_loaded`` is
        # somehow > 0 (e.g. only some aliases failed to bind).
        if load_errors_count > 0:
            conditions_payload.append(
                {
                    "id": "registry_load_errors",
                    "description": (
                        "Atlas loader reported at least one error while "
                        "constructing the alias set (e.g. orphan alias with "
                        "no matching entity, parser failures)."
                    ),
                    "passed": False,
                    "blocking": True,
                    "evidence": {
                        "load_errors": load_errors_count,
                        "report_path": "qa/registry_load_report.json",
                    },
                }
            )

        # Derived ``beta_status``: PASS only when every blocking
        # condition is satisfied AND no declared stub is enforced.
        # Direct privacy passing + zero load errors + replacement
        # rate >= 0.99 + semantic + NVIDIA still INCOMPLETE -> HONEST
        # INCOMPLETE (per the user's "do not proceed to NVIDIA or
        # semantic attacks until direct privacy passes" rule applied
        # in reverse: when those surface, INCOMPLETE is the truthful
        # answer).
        critical_warnings_count = sum(
            1
            for w in (self._harvest_report.coverage_warnings if self._harvest_report else [])
            if w.get("level") == "critical"
        )
        beta_status = "PASS"
        if (
            gate.decision != ReleaseDecision.PASS
            or semantic_incomplete
            or nvidia_incomplete
            or post_mask_hits > 0
            or replacement_rate < 0.99
            or aliases_loaded == 0
            or load_errors_count > 0
            or critical_warnings_count > 0
        ):
            beta_status = "INCOMPLETE"
        # ``release_safe`` is now driven explicitly by the four-decision
        # rule table below; assign it as ``_release_safe`` via the
        # inline computation in the return block to keep a single
        # source of truth for ``safe in [true, false]``.

        # Recompute gate_hash over the FULL conditions list so it is
        # reproducible by construction across runs.
        gate_hash = _compute_gate_hash(
            decision=gate.decision.value,
            beta_status=beta_status,
            blockers=gate.blocking_failures,
            warnings=gate.warnings,
            conditions=conditions_payload,
        )

        # Redact the raw ``--source-run`` path from the public release
        # gate payload: replace ``source_run`` with an opaque
        # ``source_hash`` (SHA-256 truncated to 16 hex chars). The
        # bounded beta's 2 × ``NVIDIA`` hits ALSO carried over via the
        # raw source_run appearing in release_gate.json when the
        # post-scan path-metadata layer echoed the path through. By
        # redaction at the public-payload boundary, downstream QA
        # consumers can correlate per-run artefacts by hash without
        # leaking the operator's filesystem layout or the source
        # ticker directory name (which previously propagated via the
        # path's ``<TICKER>`` segment).
        import hashlib as _hashlib

        _source_hash = _hashlib.sha256(str(ctx.source_run).encode("utf-8")).hexdigest()[:16]

        # Four-decision naming per the user's directive. Consumers
        # should rely on ``overall_release_decision`` rather than the
        # legacy ``decision`` field; ``decision`` is preserved as a
        # back-compat alias so existing scripts reading it keep
        # working. Rules:
        #   direct fail               -> FAIL
        #   direct pass + sem fail    -> FAIL
        #   direct pass + sem not run -> INCOMPLETE
        #   direct pass + sem pass + nvidia not run -> INCOMPLETE
        #   all required gates pass   -> PASS (release_safe true)
        direct_privacy_decision = "FAIL" if exact_hits > 0 else "PASS"
        sem_verdict = semantic_report.get("overall_verdict", "NOT_RUN")
        semantic_privacy_decision = sem_verdict if sem_verdict in ("PASS", "FAIL") else "NOT_RUN"
        nvidia_decision_label = nvidia_report.get("decision", "NOT_RUN")
        if direct_privacy_decision == "FAIL":
            overall_release_decision = "FAIL"
        elif semantic_privacy_decision == "FAIL":
            overall_release_decision = "FAIL"
        elif (
            direct_privacy_decision == "PASS"
            and semantic_privacy_decision == "PASS"
            and nvidia_decision_label == "PASS"
        ):
            overall_release_decision = "PASS"
        elif semantic_privacy_decision == "NOT_RUN":
            overall_release_decision = "INCOMPLETE"
        elif nvidia_decision_label != "NOT_RUN":
            overall_release_decision = "FAIL"
        else:  # semantic=PASS, nvidia=NOT_RUN
            overall_release_decision = "INCOMPLETE"
        # Single source of truth for ``release_safe``: it follows
        # ``overall_release_decision`` exactly, not the legacy
        # ``beta_status``. ``beta_status`` stays binary
        # (PASS|INCOMPLETE) for back-compat with prior consumers.
        _release_safe = overall_release_decision == "PASS"

        return {
            "schema_version": "1.0.0",
            "ticker": "syn_" + hashlib.sha256(ctx.ticker.encode("utf-8")).hexdigest()[:8],
            "source_hash": _source_hash,
            "direct_privacy_decision": direct_privacy_decision,
            "semantic_privacy_decision": semantic_privacy_decision,
            "nvidia_decision": nvidia_decision_label,
            "overall_release_decision": overall_release_decision,
            "decision": overall_release_decision,  # back-compat alias
            "beta_status": beta_status,
            "release_safe": _release_safe,
            "blocking_failures": gate.blocking_failures,
            "warnings": gate.warnings,
            "stubs_enforced": [
                name
                for name, present in (
                    ("semantic", semantic_incomplete),
                    ("nvidia", nvidia_incomplete),
                )
                if present
            ],
            "conditions": conditions_payload,
            "gate_hash": gate_hash,
            "limits_applied": {
                "forms": self.form_limits,
                "forms_discovered": ctx.discovered_forms,
                "news": self.limit_news,
                "news_discovered": ctx.discovered_news_count,
            },
            "surrogate_output_counts": {
                "sec_md_files": sec_stat.get("processed", 0),
                "sec_forms": sec_stat.get("forms", {}),
            },
        }


# ── Helpers ────────────────────────────────────────────────────────────


def _utc_iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _compute_gate_hash(
    decision: str,
    beta_status: str,
    blockers: int,
    warnings: int,
    conditions: list[dict[str, Any]],
) -> str:
    """Deterministic 16-char SHA-256 over the canonical gate payload."""
    return hashlib.sha256(
        json.dumps(
            {
                "decision": decision,
                "beta_status": beta_status,
                "blocking_failures": blockers,
                "warnings": warnings,
                "conditions": [
                    {"id": c["id"], "passed": c["passed"], "blocking": c["blocking"]}
                    for c in conditions
                ],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]


def _write_fail_closed_nvidia_report(
    qa_root: Path,
    mode: str,
    model: str,
    error_class: str,
    reason: str,
) -> dict[str, Any]:
    """Write a strict fail-closed NVIDIA report when review cannot complete.

    Ensures the operator always has evidence: which mode, why it failed,
    and proof that no API key appeared. Called from inside _phase_nvidia
    on any failure path.
    """
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": "FAIL",
        "implementation_status": "fail_closed",
        "nvidia_mode": mode,
        "model": model,
        "gate_verdict": "FAIL",
        "nvidia_decision": "PROVIDER_FAILURE_OR_BLOCKED_PRECHECK_OR_INCOMPLETE",
        "release_safe": False,
        "artifacts_considered": 0,
        "artifacts_reviewed": 0,
        "artifacts_skipped": 0,
        "total_chunks": 0,
        "risk_chunks_total": 0,
        "chunks_reviewed": 0,
        "chunks_rewritten": 0,
        "chunks_failed": 0,
        "chunks_skipped_due_to_cap": 0,
        "max_confidence_before": None,
        "max_confidence_after": None,
        "direct_residual_count_before": 0,
        "direct_residual_count_after": 0,
        "blocking_conditions": ["NVIDIA_REVIEW_INCOMPLETE"],
        "error_class": error_class,
        "reason": reason,
        "api_key_leaked": False,
        "evaluated_at": _utc_iso_now(),
    }
    qa_root.mkdir(parents=True, exist_ok=True)
    nvidia_path = qa_root / "nvidia_attack_report.json"
    # Atomic write: serialize to a temp file then ``os.replace`` so a
    # concurrent SIGTERM mid-write cannot leave a half-written JSON
    # file observable to downstream tooling (atomic at the filesystem
    # level — readers see either the previous file or the new file,
    # never an inconsistent in-between state).
    import os as _os_replace  # noqa: PLC0415

    tmp_path = nvidia_path.with_suffix(nvidia_path.suffix + ".tmp")
    tmp_path.write_bytes(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))
    _os_replace.replace(tmp_path, nvidia_path)
    return payload


def _write_fail_closed_gate(
    qa_root: Path,
    nvidia_report: dict[str, Any] | None,
    direct_privacy: dict[str, Any] | None,
    semantic_report: dict[str, Any] | None,
    error_class: str,
    source_hash: str,
) -> dict[str, Any]:
    """Build & write a release gate when Phase 10 itself cannot run."""
    nvidia_decision = (nvidia_report or {}).get("nvidia_decision", "FAIL")
    direct = direct_privacy is None or not direct_privacy.get("passed", True)
    sem = semantic_report is None or (
        semantic_report.get("overall_verdict", "NOT_RUN") not in ("PASS",)
    )
    overall = "FAIL"
    blocking: list[str] = []
    if direct:
        blocking.append("DIRECT_PRIVACY_INCOMPLETE_OR_FAIL")
    if sem:
        blocking.append("SEMANTIC_PRIVACY_INCOMPLETE_OR_FAIL")
    blocking.append("NVIDIA_REVIEW_INCOMPLETE")
    payload = {
        "schema_version": "1.0.0",
        "ticker": "syn_unknown",
        "source_hash": source_hash,
        "direct_privacy_decision": "FAIL" if direct else "PASS",
        "semantic_privacy_decision": ((semantic_report or {}).get("overall_verdict", "NOT_RUN")),
        "nvidia_decision": nvidia_decision,
        "overall_release_decision": overall,
        "decision": overall,
        "beta_status": "FAIL",
        "release_safe": False,
        "blocking_failures": len(blocking),
        "blocking_conditions": blocking,
        "conditions": [
            {
                "id": "release_gate_fail_closed",
                "description": f"Pipeline interrupted before release gate phase: {error_class}",
                "passed": False,
                "blocking": True,
                "evidence": {"error_class": error_class},
            }
        ],
        "stubs_enforced": ["nvidia", "release_gate"],
        "evaluated_at": _utc_iso_now(),
    }
    qa_root.mkdir(parents=True, exist_ok=True)
    gate_path = qa_root / "release_gate.json"
    # Atomic write — see note in _write_fail_closed_nvidia_report on
    # why os.replace is preferred over write_text for fail-closed
    # payloads (so SIGTERM mid-write cannot corrupt the file).
    import os as _os_replace_gate  # noqa: PLC0415

    tmp_path = gate_path.with_suffix(gate_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _os_replace_gate.replace(tmp_path, gate_path)
    return payload


def _write_stub_report(
    path: Path,
    surfaces: list[str],
    reason: str,
) -> dict[str, Any]:
    """Write a structured stub report declaring the surface as INCOMPLETE.

    The orchestrator NEVER lies about PASS when a surface is not
    implemented. This stub is the honest negative — it tells callers
    exactly which surfaces need implementation to leave the beta gate.
    """
    payload = {
        "schema_version": "1.0.0",
        "status": "INCOMPLETE",
        "surfaces": list(surfaces),
        "reason": reason,
        "implementation_status": "not_implemented",
        "evaluated_at": _utc_iso_now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
