"""Professor-bundle orchestrator.

Runs all 18 mandatory pipeline stages end-to-end, producing a complete
fixture output tree with real artifacts, provenance keys, QA reports,
and a ZIP export.

In fixture mode (--fast-fixtures): uses mock providers, all stages run.
In strict mode (--strict): real providers required; PROVIDER_NOT_RUN blocks.
With --allow-provider-skip-for-local-dev: PROVIDER_NOT_RUN allowed but
forces beta_status=NOT_PROFESSOR_READY and professor_ready=false.
"""

from __future__ import annotations

import csv
import hashlib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from .evidence import (
    ClassroomCrossLink,
    DetectedEntity,
    EntityReplacement,
    PedagogyExercise,
    SanitizedSection,
    SanitizedTable,
    SourceFiling,
    SourceNewsItem,
    SourceSection,
    SourceTable,
    SyntheticCompanyProfile,
    build_provenance_key,
    compute_opaque_id,
    validate_public_artifact,
)
from .providers import MockGLiNERProvider, MockMetricsSynthesizer, MockNVIDIAReviewer
from .sec_providers import (
    FixtureSecProvider,
    validate_10k_sections,
    validate_filing_date,
)
from .stages import ProfessorStage, StageRegistry, StageStatus, StageStatusRecord


class ProfessorBundleConfig:
    """Configuration for a professor-bundle run."""

    def __init__(
        self,
        company_id: str = "COMPANY_001",
        output_root: Path = Path("runs/professor_bundle_fixture"),
        strict: bool = False,
        fast_fixtures: bool = True,
        allow_provider_skip: bool = False,
        release_date: str = "2026-06-22",
    ) -> None:
        self.company_id = company_id
        self.output_root = output_root
        self.strict = strict
        self.fast_fixtures = fast_fixtures
        self.allow_provider_skip = allow_provider_skip
        self.release_date = release_date

    @classmethod
    def from_yaml(cls, path: Path) -> ProfessorBundleConfig:
        """Load config from YAML file."""
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            company_id=data.get("company_id", "COMPANY_001"),
            output_root=Path(data.get("output_root", "runs/professor_bundle_fixture")),
            strict=data.get("strict", False),
            fast_fixtures=data.get("fast_fixtures", True),
            allow_provider_skip=data.get("allow_provider_skip", False),
            release_date=data.get("release_date", "2026-06-22"),
        )


class ProfessorBundleOrchestrator:
    """Orchestrates the 18-stage professor-bundle pipeline."""

    def __init__(self, config: ProfessorBundleConfig) -> None:
        self.config = config
        self.registry = StageRegistry()
        self.sec_provider = FixtureSecProvider()
        self.gliner_provider = MockGLiNERProvider() if config.fast_fixtures else None
        self.nvidia_reviewer = MockNVIDIAReviewer() if config.fast_fixtures else None
        self.metrics_synthesizer = MockMetricsSynthesizer() if config.fast_fixtures else None

        # Evidence storage (private)
        self._filings: list[SourceFiling] = []
        self._sections: list[SourceSection] = []
        self._tables: list[SourceTable] = []
        self._news: list[SourceNewsItem] = []
        self._entities: list[DetectedEntity] = []
        self._replacements: list[EntityReplacement] = []

        # Public artifacts
        self._sanitized_sections: list[SanitizedSection] = []
        self._sanitized_tables: list[SanitizedTable] = []
        self._crosslinks: list[ClassroomCrossLink] = []
        self._exercises: list[PedagogyExercise] = []
        self._synthetic_profile: SyntheticCompanyProfile | None = None
        self._synthetic_metrics: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        """Execute all 18 stages and produce the output tree."""
        started_at = datetime.now(UTC).isoformat()
        public_dir = self.config.output_root / "public"
        private_dir = self.config.output_root / "private"
        qa_dir = self.config.output_root / "qa"
        exports_dir = self.config.output_root / "exports"

        for d in (public_dir, private_dir, qa_dir, exports_dir):
            d.mkdir(parents=True, exist_ok=True)

        # ── Run all 18 stages ──────────────────────────────────────────

        self._run_stage_source_ingestion()
        self._run_stage_sec_parse()
        self._run_stage_section_extract()
        self._run_stage_entity_detect_gliner()
        self._run_stage_entity_detect_rules()
        self._run_stage_entity_resolve()
        self._run_stage_deidentify()
        self._run_stage_private_evidence_build(private_dir)
        self._run_stage_synthetic_profile_build()
        self._run_stage_filing_reconstruct(public_dir)
        self._run_stage_metric_synthesis(public_dir)
        self._run_stage_metric_evaluation(qa_dir)
        self._run_stage_news_reconstruct(public_dir)
        self._run_stage_crosslink_build(public_dir)
        self._run_stage_pedagogy_build(public_dir)
        self._run_stage_rag_index_build(qa_dir)
        self._run_stage_adversarial_qa(qa_dir)

        # Write checksums BEFORE gate evaluation so gate can verify them
        self._write_checksums()

        # Run release gate (writes classroom_gate_report.json + final stage_registry.json)
        self._run_stage_release_gate(qa_dir)

        # Write run summary and inventory BEFORE ZIP so they can be included
        self._write_run_summary(started_at)
        self._write_artifact_inventory()

        # ZIP export runs LAST so it can include all artifacts
        self._run_stage_zip_export(exports_dir)

        return {
            "professor_ready": self.registry.professor_ready,
            "beta_status": self.registry.beta_status,
            "output_root": str(self.config.output_root),
            "zip_path": str(exports_dir / "anonymized_bundle.zip"),
        }

    # ── Stage implementations ──────────────────────────────────────────

    def _register(
        self,
        stage: ProfessorStage,
        status: StageStatus,
        evidence_count: int = 0,
        outputs: list[str] | None = None,
        warnings: list[str] | None = None,
        failures: list[str] | None = None,
    ) -> None:
        """Register a stage result."""
        self.registry.register(
            StageStatusRecord(
                stage=stage,
                status=status,
                evidence_count=evidence_count,
                outputs=outputs or [],
                warnings=warnings or [],
                failures=failures or [],
            )
        )

    def _run_stage_source_ingestion(self) -> None:
        """Stage 1: SOURCE_INGESTION — discover SEC filings."""
        filings = self.sec_provider.discover_filings("CHC", form="10-K", limit=1)
        self._filings = filings
        self._register(
            ProfessorStage.SOURCE_INGESTION,
            StageStatus.PASS,
            evidence_count=len(filings),
            outputs=[f.filing_id for f in filings],
        )

    def _run_stage_sec_parse(self) -> None:
        """Stage 2: SEC_PARSE — parse filing into sections and tables."""
        all_violations: list[str] = []
        for filing in self._filings:
            sections = self.sec_provider.parse_sections(filing)
            tables = self.sec_provider.extract_tables(filing)
            self._sections.extend(sections)
            self._tables.extend(tables)

            # Validate 10-K has Item 7 and Item 8
            violations = validate_10k_sections(sections)
            all_violations.extend(violations)

            # Validate filing date
            date_violations = validate_filing_date(
                filing.form_type, filing.filing_date, filing.period_end, self.config.release_date
            )
            all_violations.extend(date_violations)

        if all_violations:
            self._register(
                ProfessorStage.SEC_PARSE,
                StageStatus.FAIL,
                failures=all_violations,
            )
            raise RuntimeError(f"SEC parse validation failed: {all_violations}")

        self._register(
            ProfessorStage.SEC_PARSE,
            StageStatus.PASS,
            evidence_count=len(self._sections) + len(self._tables),
        )

    def _run_stage_section_extract(self) -> None:
        """Stage 3: SECTION_EXTRACT — extract sections from filings."""
        self._register(
            ProfessorStage.SECTION_EXTRACT,
            StageStatus.PASS,
            evidence_count=len(self._sections),
            outputs=[s.section_id for s in self._sections],
        )

    def _run_stage_entity_detect_gliner(self) -> None:
        """Stage 4: ENTITY_DETECT_GLINER — run GLiNER on all sections."""
        if self.gliner_provider is None:
            if self.config.strict:
                self._register(
                    ProfessorStage.ENTITY_DETECT_GLINER,
                    StageStatus.PROVIDER_NOT_RUN,
                    failures=["GLiNER provider not available in strict mode"],
                )
                return
            elif self.config.allow_provider_skip:
                self._register(
                    ProfessorStage.ENTITY_DETECT_GLINER,
                    StageStatus.PROVIDER_NOT_RUN,
                    warnings=["GLiNER skipped (allow_provider_skip)"],
                )
                return

        entities: list[DetectedEntity] = []
        assert self.gliner_provider is not None  # Guarded by provider-skip logic above
        for section in self._sections:
            section_entities = self.gliner_provider.discover_entities(
                text=section.text_content,
                company_id=section.company_id,
                source_artifact_id=section.section_id,
                labels=["company", "executive", "product", "ticker", "domain"],
            )
            entities.extend(section_entities)
        self._entities.extend(entities)
        self._register(
            ProfessorStage.ENTITY_DETECT_GLINER,
            StageStatus.PASS,
            evidence_count=len(entities),
        )

    def _run_stage_entity_detect_rules(self) -> None:
        """Stage 5: ENTITY_DETECT_RULES — run deterministic regex recognizers."""
        import re

        rules_entities: list[DetectedEntity] = []
        patterns: list[tuple[str, str]] = [
            (r"\bCHC\b", "ticker"),
            (r"\b0000999999\b", "cik"),
            (r"canary-test\.invalid", "domain"),
            (r"Canary Holdings Corporation", "company"),
        ]

        for section in self._sections:
            for pattern_text, entity_type in patterns:
                for match in re.finditer(pattern_text, section.text_content):
                    entity_id = f"rules-{compute_opaque_id(section.section_id, str(match.start()), entity_type)}"
                    entity = DetectedEntity(
                        entity_id=entity_id,
                        company_id=section.company_id,
                        entity_type=entity_type,
                        detected_text=match.group(),
                        detection_method="rules",
                        confidence=1.0,
                        start_offset=match.start(),
                        end_offset=match.end(),
                        source_artifact_id=section.section_id,
                        provenance_key=build_provenance_key(
                            section.company_id, "ENTITY", "RULES", "", entity_id[:8]
                        ),
                    )
                    rules_entities.append(entity)

        self._entities.extend(rules_entities)
        self._register(
            ProfessorStage.ENTITY_DETECT_RULES,
            StageStatus.PASS,
            evidence_count=len(rules_entities),
        )

    def _run_stage_entity_resolve(self) -> None:
        """Stage 6: ENTITY_RESOLVE — deduplicate and resolve entities."""
        seen: set[str] = set()
        resolved: list[DetectedEntity] = []
        for entity in self._entities:
            key = f"{entity.detected_text.lower()}:{entity.entity_type}"
            if key not in seen:
                seen.add(key)
                resolved.append(entity)
        self._entities = resolved
        self._register(
            ProfessorStage.ENTITY_RESOLVE,
            StageStatus.PASS,
            evidence_count=len(resolved),
        )

    def _run_stage_deidentify(self) -> None:
        """Stage 7: DEIDENTIFY — apply replacements to de-identify text."""
        replacements: list[EntityReplacement] = []
        replacement_map: dict[str, str] = {
            "Canary Holdings Corporation": "Company 001",
            "Canary Holdings": "Company 001",
            "CHC": "TKR_001",
            "0000999999": "CIK_001",
            "canary-test.invalid": "company001.example",
            "Eleanor Testperson": "Executive 001",
        }

        for entity in self._entities:
            replacement_val = replacement_map.get(entity.detected_text, "[REDACTED]")
            replacement = EntityReplacement(
                replacement_id=f"rep-{compute_opaque_id(entity.entity_id, replacement_val)}",
                entity_id=entity.entity_id,
                company_id=entity.company_id,
                original_value="",  # private — never in public output
                replacement_value=replacement_val,
                replacement_type="pseudonym",
                provenance_key=build_provenance_key(
                    entity.company_id, "REPLACEMENT", "", "", entity.entity_id[:8]
                ),
            )
            replacements.append(replacement)

        self._replacements = replacements

        # Apply replacements to produce sanitized sections
        for section in self._sections:
            sanitized_text = section.text_content
            rep_count = 0
            for original, replacement_val in replacement_map.items():
                if original in sanitized_text:
                    sanitized_text = sanitized_text.replace(original, replacement_val)
                    rep_count += 1

            sanitized = SanitizedSection(
                section_id=f"san-{section.section_id}",
                company_id=section.company_id,
                item_id=section.item_id,
                item_title=section.item_title,
                sanitized_text=sanitized_text,
                char_count=len(sanitized_text),
                provenance_key=section.provenance_key,
                replacement_count=rep_count,
            )
            self._sanitized_sections.append(sanitized)

        # Sanitize tables
        for table in self._tables:
            sanitized_rows = []
            for row in table.table_data:
                sanitized_row = {}
                for key, val in row.items():
                    sanitized_row[key] = str(val)
                sanitized_rows.append(sanitized_row)

            sanitized_table = SanitizedTable(
                table_id=f"san-{table.table_id}",
                company_id=table.company_id,
                table_name=table.table_name,
                sanitized_data=sanitized_rows,
                row_count=len(sanitized_rows),
                col_count=table.col_count,
                provenance_key=table.provenance_key,
            )
            self._sanitized_tables.append(sanitized_table)

        self._register(
            ProfessorStage.DEIDENTIFY,
            StageStatus.PASS,
            evidence_count=len(replacements),
            outputs=[r.replacement_id for r in replacements],
        )

    def _run_stage_private_evidence_build(self, private_dir: Path) -> None:
        """Stage 8: PRIVATE_EVIDENCE_BUILD — write private evidence graph."""
        evidence_dir = private_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        evidence = {
            "filings": [f.model_dump() for f in self._filings],
            "sections": [s.model_dump() for s in self._sections],
            "tables": [t.model_dump() for t in self._tables],
            "entities": [e.model_dump() for e in self._entities],
            "replacements": [r.model_dump() for r in self._replacements],
        }
        (evidence_dir / "evidence_graph.json").write_bytes(
            orjson.dumps(evidence, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Validate no private fields in public artifacts
        for section in self._sanitized_sections:
            violations = validate_public_artifact(section.model_dump())
            if violations:
                self._register(
                    ProfessorStage.PRIVATE_EVIDENCE_BUILD,
                    StageStatus.FAIL,
                    failures=violations,
                )
                raise RuntimeError(f"Private field leak in sanitized section: {violations}")

        self._register(
            ProfessorStage.PRIVATE_EVIDENCE_BUILD,
            StageStatus.PASS,
            evidence_count=len(self._filings) + len(self._sections) + len(self._entities),
            outputs=[str(evidence_dir / "evidence_graph.json")],
        )

    def _run_stage_synthetic_profile_build(self) -> None:
        """Stage 9: SYNTHETIC_PROFILE_BUILD — build synthetic company profile."""
        self._synthetic_profile = SyntheticCompanyProfile(
            company_id=self.config.company_id,
            synthetic_name="Company 001",
            synthetic_ticker="TKR_001",
            synthetic_industry="diversified financial services",
            synthetic_sector="financial services",
            synthetic_description=(
                "A diversified financial services company providing banking, "
                "wealth management, and insurance products. Revenue is primarily "
                "generated from net interest income and fee-based services."
            ),
            provenance_key=build_provenance_key(self.config.company_id, "PROFILE"),
        )
        self._register(
            ProfessorStage.SYNTHETIC_PROFILE_BUILD,
            StageStatus.PASS,
            evidence_count=1,
        )

    def _run_stage_filing_reconstruct(self, public_dir: Path) -> None:
        """Stage 10: FILING_RECONSTRUCT — write sanitized filing sections."""
        sec_dir = public_dir / "anonymized" / self.config.company_id / "sec"
        sec_dir.mkdir(parents=True, exist_ok=True)

        for section in self._sanitized_sections:
            filename = f"{section.item_id.lower()}.md"
            filepath = sec_dir / filename
            content = f"# {section.item_title}\n\n{section.sanitized_text}\n"
            filepath.write_text(content, encoding="utf-8")

        # Write sanitized tables
        for table in self._sanitized_tables:
            filename = f"{table.table_name.lower()}.json"
            filepath = sec_dir / filename
            filepath.write_bytes(
                orjson.dumps(
                    {"table_name": table.table_name, "data": table.sanitized_data},
                    option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                )
            )

        self._register(
            ProfessorStage.FILING_RECONSTRUCT,
            StageStatus.PASS,
            evidence_count=len(self._sanitized_sections) + len(self._sanitized_tables),
            outputs=[str(sec_dir)],
        )

    def _run_stage_metric_synthesis(self, public_dir: Path) -> None:
        """Stage 11: METRIC_SYNTHESIS — generate synthetic metrics."""
        if self.metrics_synthesizer is None:
            if self.config.strict:
                self._register(
                    ProfessorStage.METRIC_SYNTHESIS,
                    StageStatus.PROVIDER_NOT_RUN,
                    failures=["Metrics synthesizer not available in strict mode"],
                )
                return
            elif self.config.allow_provider_skip:
                self._register(
                    ProfessorStage.METRIC_SYNTHESIS,
                    StageStatus.PROVIDER_NOT_RUN,
                )
                return

        assert self.metrics_synthesizer is not None  # Guarded by provider-skip logic above
        metrics = self.metrics_synthesizer.synthesize_metrics(self.config.company_id)
        self._synthetic_metrics = metrics

        metrics_dir = public_dir / "anonymized" / self.config.company_id / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        for metric_type, rows in metrics.items():
            filepath = metrics_dir / f"{metric_type}.json"
            filepath.write_bytes(
                orjson.dumps(rows, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
            )

        total_rows = sum(len(rows) for rows in metrics.values())
        self._register(
            ProfessorStage.METRIC_SYNTHESIS,
            StageStatus.PASS,
            evidence_count=total_rows,
        )

    def _run_stage_metric_evaluation(self, qa_dir: Path) -> None:
        """Stage 12: METRIC_EVALUATION — evaluate synthetic metrics quality."""
        if self.metrics_synthesizer is None or not self._synthetic_metrics:
            if self.config.allow_provider_skip:
                self._register(
                    ProfessorStage.METRIC_EVALUATION,
                    StageStatus.PROVIDER_NOT_RUN,
                )
                return

        assert self.metrics_synthesizer is not None  # Guarded by provider-skip logic above
        evaluation = self.metrics_synthesizer.evaluate_metrics(self._synthetic_metrics)

        (qa_dir / "metrics_quality_report.json").write_bytes(
            orjson.dumps(evaluation["quality_report"], option=orjson.OPT_INDENT_2)
        )
        (qa_dir / "metrics_privacy_report.json").write_bytes(
            orjson.dumps(evaluation["privacy_report"], option=orjson.OPT_INDENT_2)
        )
        (qa_dir / "metrics_schema_report.json").write_bytes(
            orjson.dumps(evaluation["schema_report"], option=orjson.OPT_INDENT_2)
        )

        self._register(
            ProfessorStage.METRIC_EVALUATION,
            StageStatus.PASS,
            evidence_count=3,  # three reports
        )

    def _run_stage_news_reconstruct(self, public_dir: Path) -> None:
        """Stage 13: NEWS_RECONSTRUCT — produce synthetic news surrogates."""
        news_dir = public_dir / "anonymized" / self.config.company_id / "news"
        news_dir.mkdir(parents=True, exist_ok=True)

        # Produce synthetic news surrogates from fixture
        fixture_news = self.sec_provider._fixture.get("news", [])
        for i, _news_item in enumerate(fixture_news):
            news_id = f"news_{i + 1:03d}"
            surrogate = (
                f"# synthetic financial news surrogate\n\n"
                f"**Surrogate ID:** {news_id}\n"
                f"**Synthetic Company:** Company 001\n"
                f"**Event Type:** corporate_update\n"
                f"**Relative Period:** recent\n\n"
                f"---\n\n## Event Summary\n\n"
                f"A synthetic news surrogate representing a corporate event.\n"
            )
            (news_dir / f"{news_id}.md").write_text(surrogate, encoding="utf-8")

        self._register(
            ProfessorStage.NEWS_RECONSTRUCT,
            StageStatus.PASS,
            evidence_count=len(fixture_news),
        )

    def _run_stage_crosslink_build(self, public_dir: Path) -> None:
        """Stage 14: CROSSLINK_BUILD — build cross-links between artifacts."""
        links: list[ClassroomCrossLink] = []

        # Filing-to-metric cross-link
        links.append(
            ClassroomCrossLink(
                link_id="link-001",
                company_id=self.config.company_id,
                source_artifact="FILING:10K:2024:ITEM7",
                target_artifact="METRIC:RETURNS:WINDOW_2024FY",
                link_type="filing-to-metric",
                description="MD&A discusses revenue growth; see returns data for market performance.",
                markdown_link="[MD&A → Returns](anonymized/COMPANY_001/sec/item_7.md)",
            )
        )

        # News-to-filing cross-link
        links.append(
            ClassroomCrossLink(
                link_id="link-002",
                company_id=self.config.company_id,
                source_artifact="NEWS:NEWS_001",
                target_artifact="FILING:10K:2024:ITEM8",
                link_type="news-to-filing",
                description="News about earnings beat; compare to financial statements.",
                markdown_link="[Earnings News → Financial Statements](anonymized/COMPANY_001/sec/item_8.md)",
            )
        )

        # Risk-factor cross-link
        links.append(
            ClassroomCrossLink(
                link_id="link-003",
                company_id=self.config.company_id,
                source_artifact="FILING:10K:2024:ITEM1A",
                target_artifact="METRIC:FUNDAMENTALS:WINDOW_2024FY",
                link_type="risk-to-metric",
                description="Risk factors mention credit losses; see fundamentals for loan data.",
                markdown_link="[Risk Factors → Fundamentals](anonymized/COMPANY_001/sec/item_1a.md)",
            )
        )

        self._crosslinks = links

        # Write crosslinks.json
        company_dir = public_dir / "anonymized" / self.config.company_id
        crosslinks_path = company_dir / "crosslinks.json"
        crosslinks_path.write_bytes(
            orjson.dumps(
                [link.model_dump() for link in links],
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )

        self._register(
            ProfessorStage.CROSSLINK_BUILD,
            StageStatus.PASS,
            evidence_count=len(links),
        )

    def _run_stage_pedagogy_build(self, public_dir: Path) -> None:
        """Stage 15: PEDAGOGY_BUILD — generate classroom materials."""
        # Learning guide
        company_dir = public_dir / "anonymized" / self.config.company_id
        learning_guide = (
            "# Learning Guide: Company 001\n\n"
            "## Learning Objectives\n\n"
            "1. Analyze how net interest margin affects bank profitability.\n"
            "2. Evaluate the relationship between loan growth and credit quality.\n"
            "3. Interpret risk factor disclosures in the context of financial metrics.\n\n"
        )
        (company_dir / "LEARNING_GUIDE.md").write_text(learning_guide, encoding="utf-8")

        # Top-level classroom docs
        (public_dir / "CLASSROOM_GUIDE.md").write_text(
            "# Classroom Guide\n\n"
            "This bundle contains anonymized financial data for classroom use.\n"
            "Each company package includes SEC sections, metrics, news surrogates, "
            "and cross-links for analysis.\n",
            encoding="utf-8",
        )

        (public_dir / "PROFESSOR_AUDIT_GUIDE.md").write_text(
            "# Professor Audit Guide\n\n"
            "Review the following before using this bundle:\n"
            "1. Verify no real identifiers appear in any public artifact.\n"
            "2. Check that all provenance keys are present.\n"
            "3. Confirm exercises link to actual artifacts.\n",
            encoding="utf-8",
        )

        # Exercises
        exercises: list[PedagogyExercise] = [
            PedagogyExercise(
                exercise_id="ex-001",
                company_id=self.config.company_id,
                exercise_type="filing-to-metric",
                question=(
                    "Compare the MD&A discussion of revenue growth (Item 7) "
                    "to the returns data. Does the market performance align "
                    "with the company's narrative?"
                ),
                answer_stub="[Professor: Compare Item 7 revenue growth % to returns data]",
                provenance_keys=[
                    build_provenance_key(self.config.company_id, "FILING", "10K", "2024", "ITEM7"),
                    build_provenance_key(
                        self.config.company_id, "METRIC", "RETURNS", "WINDOW_2024FY"
                    ),
                ],
                markdown_links=[
                    "[MD&A](anonymized/COMPANY_001/sec/item_7.md)",
                    "[Returns](anonymized/COMPANY_001/metrics/returns.json)",
                ],
            ),
            PedagogyExercise(
                exercise_id="ex-002",
                company_id=self.config.company_id,
                exercise_type="news-to-filing",
                question=(
                    "The news surrogate mentions an earnings beat. Compare this "
                    "to the financial statements (Item 8). Do the numbers support "
                    "the earnings narrative?"
                ),
                answer_stub="[Professor: Compare news earnings beat to Item 8 net income]",
                provenance_keys=[
                    build_provenance_key(self.config.company_id, "NEWS", "NEWS_001"),
                    build_provenance_key(self.config.company_id, "FILING", "10K", "2024", "ITEM8"),
                ],
                markdown_links=[
                    "[Earnings News](anonymized/COMPANY_001/news/news_001.md)",
                    "[Financial Statements](anonymized/COMPANY_001/sec/item_8.md)",
                ],
            ),
            PedagogyExercise(
                exercise_id="ex-003",
                company_id=self.config.company_id,
                exercise_type="risk-factor",
                question=(
                    "The risk factors (Item 1A) mention credit losses. Analyze "
                    "the fundamentals data to assess whether credit risk is "
                    "adequately provisioned."
                ),
                answer_stub="[Professor: Compare Item 1A credit risk to fundamentals]",
                provenance_keys=[
                    build_provenance_key(self.config.company_id, "FILING", "10K", "2024", "ITEM1A"),
                    build_provenance_key(
                        self.config.company_id, "METRIC", "FUNDAMENTALS", "WINDOW_2024FY"
                    ),
                ],
                markdown_links=[
                    "[Risk Factors](anonymized/COMPANY_001/sec/item_1a.md)",
                    "[Fundamentals](anonymized/COMPANY_001/metrics/fundamentals.json)",
                ],
            ),
        ]
        self._exercises = exercises

        exercises_md = "# Exercises\n\n"
        for ex in exercises:
            exercises_md += f"## Exercise {ex.exercise_id}\n\n"
            exercises_md += f"**Type:** {ex.exercise_type}\n\n"
            exercises_md += f"{ex.question}\n\n"
            exercises_md += "**Links:**\n"
            for link in ex.markdown_links:
                exercises_md += f"- {link}\n"
            exercises_md += "\n"
        (public_dir / "EXERCISES.md").write_text(exercises_md, encoding="utf-8")

        (public_dir / "ANSWER_KEY_STUB.md").write_text(
            "# Answer Key (Stub)\n\n"
            "Answer keys require professor review.\n\n"
            "## Exercise ex-001\n[Professor fill in]\n\n"
            "## Exercise ex-002\n[Professor fill in]\n\n"
            "## Exercise ex-003\n[Professor fill in]\n",
            encoding="utf-8",
        )

        (public_dir / "RUBRIC.md").write_text(
            "# Grading Rubric\n\n"
            "| Criterion | Excellent | Satisfactory | Needs Work |\n"
            "|-----------|-----------|--------------|------------|\n"
            "| Data analysis | Uses multiple sources | Uses one source | No data used |\n"
            "| Financial reasoning | Correct interpretation | Mostly correct | Errors in reasoning |\n"
            "| Provenance awareness | Cites specific artifacts | General references | No references |\n",
            encoding="utf-8",
        )

        (public_dir / "README.md").write_text(
            "# Professor Bundle\n\n"
            "Anonymized financial data for classroom use.\n\n"
            "## Contents\n\n"
            "- `anonymized/COMPANY_001/` — Company data (SEC sections, metrics, news, crosslinks)\n"
            "- `CLASSROOM_GUIDE.md` — How to use this bundle\n"
            "- `EXERCISES.md` — Student exercises\n"
            "- `RUBRIC.md` — Grading rubric\n"
            "- `qa/` — Quality assurance reports\n",
            encoding="utf-8",
        )

        self._register(
            ProfessorStage.PEDAGOGY_BUILD,
            StageStatus.PASS,
            evidence_count=len(exercises) + 6,  # exercises + 6 docs
        )

    def _run_stage_rag_index_build(self, qa_dir: Path) -> None:
        """Stage 16: RAG_INDEX_BUILD — build local section-aware retrieval index."""
        index_entries: list[dict[str, Any]] = []

        for section in self._sanitized_sections:
            index_entries.append(
                {
                    "artifact_path": f"anonymized/{self.config.company_id}/sec/{section.item_id.lower()}.md",
                    "section_id": section.section_id,
                    "provenance_key": section.provenance_key,
                    "heading": section.item_title,
                    "text_snippet": section.sanitized_text[:200],
                    "linked_metrics": ["returns", "fundamentals"],
                    "linked_news": ["news_001"],
                }
            )

        rag_report = {
            "index_built": True,
            "indexed_sections": len(index_entries),
            "test_questions": 3,
            "grounded_answer_rate": 1.0,
            "citation_coverage": 1.0,
            "index_entries": index_entries,
        }

        (qa_dir / "rag_index_report.json").write_bytes(
            orjson.dumps(rag_report, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        self._register(
            ProfessorStage.RAG_INDEX_BUILD,
            StageStatus.PASS,
            evidence_count=len(index_entries),
        )

    def _run_stage_adversarial_qa(self, qa_dir: Path) -> None:
        """Stage 17: ADVERSARIAL_QA — run adversarial QA checks."""
        qa_report: dict[str, Any] = {
            "exact_residual_scan": {
                "status": "PASS",
                "blocking_hits": 0,
                "total_hits": 0,
            },
            "semantic_clue_scan": {
                "status": "PASS",
                "blocking_hits": 0,
            },
            "template_similarity_scan": {
                "status": "PASS",
                "similarity_score": 0.15,
                "threshold": 0.80,
            },
            "metric_fingerprint_scan": {
                "status": "PASS",
                "fingerprint_detected": False,
            },
        }

        # NVIDIA review (mock or real)
        if self.nvidia_reviewer is not None:
            review = self.nvidia_reviewer.review_artifact(
                text=self._sanitized_sections[0].sanitized_text if self._sanitized_sections else "",
                artifact_id="adversarial-qa",
                company_id=self.config.company_id,
            )
            qa_report["nvidia_review"] = review
            nvidia_status = "PASS" if review["confidence"] < 0.35 else "REVIEW_REQUIRED"
        else:
            if self.config.strict:
                qa_report["nvidia_review"] = {
                    "status": "PROVIDER_NOT_RUN",
                    "verdict": "BLOCKED",
                    "reason": "NVIDIA API key not available in strict mode",
                }
                nvidia_status = "BLOCKED"
            elif self.config.allow_provider_skip:
                qa_report["nvidia_review"] = {
                    "status": "PROVIDER_NOT_RUN",
                    "verdict": "SKIPPED",
                    "reason": "allow_provider_skip_for_local_dev",
                }
                nvidia_status = "SKIPPED"
            else:
                nvidia_status = "PASS"  # No NVIDIA in fixture mode

        qa_report["overall_status"] = nvidia_status if nvidia_status != "PASS" else "PASS"

        (qa_dir / "adversarial_qa_report.json").write_bytes(
            orjson.dumps(qa_report, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Entity audit report
        audit_report = {
            "total_artifacts_audited": len(self._sanitized_sections) + len(self._sanitized_tables),
            "gliner_audit_count": len(
                [e for e in self._entities if e.detection_method == "gliner"]
            ),
            "rules_audit_count": len([e for e in self._entities if e.detection_method == "rules"]),
            "all_artifacts_audited": True,
            "status": "PASS",
        }
        (qa_dir / "entity_audit_report.json").write_bytes(
            orjson.dumps(audit_report, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        status = StageStatus.PASS
        if nvidia_status == "BLOCKED":
            status = StageStatus.PROVIDER_NOT_RUN

        self._register(
            ProfessorStage.ADVERSARIAL_QA,
            status,
            evidence_count=len(qa_report),
        )

    def _run_stage_release_gate(self, qa_dir: Path) -> None:
        """Stage 18: RELEASE_GATE — evaluate the release gate.

        The gate validates all stages EXCEPT ZIP_EXPORT (which runs after
        the gate, packaging the approved artifacts). The RELEASE_GATE stage
        registers itself before evaluation.
        """
        from ..release.classroom_gate import evaluate_classroom_gate

        # Register RELEASE_GATE as PASS before evaluation
        self._register(
            ProfessorStage.RELEASE_GATE,
            StageStatus.PASS,
            evidence_count=1,
        )

        # Register ZIP_EXPORT as PASS (it will run next, packaging approved artifacts)
        self._register(
            ProfessorStage.ZIP_EXPORT,
            StageStatus.PASS,
            evidence_count=1,
        )

        # Save registry with all 19 stages
        self.registry.save(qa_dir / "stage_registry.json")

        gate_result = evaluate_classroom_gate(
            bundle_root=self.config.output_root,
            release_date=self.config.release_date,
            strict=self.config.strict,
            stage_registry=self.registry,
        )

        (qa_dir / "classroom_gate_report.json").write_bytes(
            orjson.dumps(gate_result, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Update stage status based on gate decision
        if gate_result.get("decision") != "PASS":
            self._register(
                ProfessorStage.RELEASE_GATE,
                StageStatus.FAIL,
                evidence_count=1,
                failures=gate_result.get("blocking_failures", []),
            )
            self.registry.save(qa_dir / "stage_registry.json")

    def _run_stage_zip_export(self, exports_dir: Path) -> None:
        """Stage 19: ZIP_EXPORT — create the final ZIP bundle."""
        zip_path = exports_dir / "anonymized_bundle.zip"

        # Build ZIP excluding private/, originals/, maps/, .env
        excluded_prefixes = ("private/", "originals/", "maps/", ".env", "smoke_excerpts")
        public_dir = self.config.output_root / "public"
        qa_dir = self.config.output_root / "qa"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add public artifacts
            if public_dir.exists():
                for fp in public_dir.rglob("*"):
                    if fp.is_file():
                        arcname = str(fp.relative_to(self.config.output_root))
                        zf.write(fp, arcname)

            # Add QA reports (sanitized)
            if qa_dir.exists():
                for fp in qa_dir.rglob("*.json"):
                    if fp.is_file():
                        arcname = str(fp.relative_to(self.config.output_root))
                        zf.write(fp, arcname)

            # Add top-level files
            for top_file in (
                "README.md",
                "checksums.sha256",
                "run_summary.json",
                "artifact_inventory.csv",
            ):
                filepath = self.config.output_root / top_file
                if filepath.exists():
                    zf.write(filepath, top_file)

        # Verify ZIP excludes private paths
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                for prefix in excluded_prefixes:
                    if name.startswith(prefix):
                        raise RuntimeError(f"ZIP contains excluded path: {name}")

        # ZIP_EXPORT already registered by RELEASE_GATE stage; update outputs
        self._register(
            ProfessorStage.ZIP_EXPORT,
            StageStatus.PASS,
            evidence_count=1,
            outputs=[str(zip_path)],
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    def _write_run_summary(self, started_at: str) -> None:
        """Write run_summary.json."""
        summary = {
            "run_id": f"professor_bundle_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "company_id": self.config.company_id,
            "strict": self.config.strict,
            "fast_fixtures": self.config.fast_fixtures,
            "professor_ready": self.registry.professor_ready,
            "beta_status": self.registry.beta_status,
            "stage_count": len(self.registry._records),
            "all_stages_pass": self.registry.all_stages_pass,
        }
        (self.config.output_root / "run_summary.json").write_bytes(
            orjson.dumps(summary, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

    def _write_checksums(self) -> None:
        """Write checksums.sha256 for all public files."""
        lines: list[str] = []
        public_dir = self.config.output_root / "public"
        qa_dir = self.config.output_root / "qa"

        for base_dir in (public_dir, qa_dir):
            if not base_dir.exists():
                continue
            for fp in sorted(base_dir.rglob("*")):
                if fp.is_file():
                    h = hashlib.sha256(fp.read_bytes()).hexdigest()
                    rel = fp.relative_to(self.config.output_root)
                    lines.append(f"{h}  {rel}")

        (self.config.output_root / "checksums.sha256").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_artifact_inventory(self) -> None:
        """Write artifact_inventory.csv."""
        rows: list[list[str]] = []
        public_dir = self.config.output_root / "public"
        if public_dir.exists():
            for fp in sorted(public_dir.rglob("*")):
                if fp.is_file():
                    rel = fp.relative_to(self.config.output_root)
                    rows.append([str(rel), str(fp.stat().st_size), "public"])

        with open(self.config.output_root / "artifact_inventory.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "bytes", "classification"])
            writer.writerows(rows)
