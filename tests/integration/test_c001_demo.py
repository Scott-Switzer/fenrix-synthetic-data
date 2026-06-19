"""End-to-end offline C001 extraction demonstration.

Runs the complete fixture pipeline:

1. Discover C001 filings from fixtures
2. Load raw HTML from fixtures
3. Convert HTML to normalized markdown
4. Segment into Item sections
5. Verify hashes and lineage
6. Verify checkpoint resume

No network operations occur.
"""

from pathlib import Path

from fenrix_synthetic.extraction.converter import HtmlFilingExtractor
from fenrix_synthetic.extraction.segmenter import FilingSegmenter
from fenrix_synthetic.schemas import StageName, StageStatus
from fenrix_synthetic.schemas.checkpoints import (
    CheckpointStatus,
    OutputArtifact,
    StageCheckpoint,
)
from fenrix_synthetic.sec import SECClient
from fenrix_synthetic.sec.transport import FixtureTransport
from fenrix_synthetic.storage.checkpoints import (
    save_checkpoint,
    validate_checkpoint,
)
from fenrix_synthetic.storage.checksums import compute_file_hash, write_sidecar
from fenrix_synthetic.storage.hashing import hash_object, hash_string

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sec"


class TestC001EndToEnd:
    """End-to-end offline C001 extraction demonstration."""

    def test_full_pipeline(self, temp_dir: Path):
        """Run the complete C001 fixture pipeline end-to-end."""
        company = "C001"
        pipeline_version = "0.1.0"
        ticker = "SYNTH"

        # ── Stage 1: Discover ────────────────────────────────────────
        transport = FixtureTransport(FIXTURE_DIR)
        client = SECClient(transport)

        cik = client.resolve_cik(ticker)
        assert cik == "0001234567", f"Expected CIK 0001234567, got {cik}"

        filings = client.get_filings(ticker, form="10-K", limit=1)
        assert len(filings) == 1, f"Expected 1 filing, got {len(filings)}"

        filing = filings[0]
        assert filing["form"] == "10-K"
        assert filing["accessionNumber"] == "0001234567-24-000001"
        assert filing["filingDate"] == "2024-11-15"

        accession = filing["accessionNumber"]
        primary_doc = filing.get("primaryDocument", "")
        filing_url = SECClient.build_filing_url(cik, accession, primary_doc)
        assert "sec.gov" in filing_url

        # ── Stage 2: Download raw HTML ───────────────────────────────
        raw_dir = temp_dir / "raw" / company
        raw_dir.mkdir(parents=True)
        raw_filename = f"{accession.replace('-', '')}.html"
        raw_path = raw_dir / raw_filename

        resp = transport.get_bytes(filing_url)
        raw_path.write_bytes(resp.content)
        raw_sha256 = compute_file_hash(raw_path)
        assert len(raw_sha256) == 64

        sidecar_path = write_sidecar(raw_path)
        assert sidecar_path.exists()

        # Verify sidecar
        from fenrix_synthetic.storage.checksums import validate_sidecar

        assert validate_sidecar(raw_path) is True

        # ── Stage 3: Extract (HTML → Markdown) ────────────────────────
        html_content = raw_path.read_text(encoding="utf-8")
        extractor = HtmlFilingExtractor()
        result = extractor.extract(html_content)

        normalized_text = result["text"]
        assert "Item 1. Business" in normalized_text
        assert "Item 1A. Risk Factors" in normalized_text
        assert "Item 2. Properties" in normalized_text

        bronze_dir = temp_dir / "bronze" / company
        bronze_dir.mkdir(parents=True)
        text_path = bronze_dir / f"{accession.replace('-', '')}.md"
        text_path.write_text(normalized_text, encoding="utf-8")
        text_sha256 = compute_file_hash(text_path)

        # ── Stage 4: Segment ─────────────────────────────────────────
        segmenter = FilingSegmenter()
        sections = segmenter.segment(normalized_text)

        assert len(sections) >= 2, f"Expected >=2 sections, got {len(sections)}"

        sections_path = bronze_dir / f"{accession.replace('-', '')}_sections.json"
        sections_data = [
            {"item": s.item, "title": s.title, "char_count": s.char_count} for s in sections
        ]
        import orjson

        sections_path.write_bytes(
            orjson.dumps(sections_data, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # ── Stage 5: Save checkpoints ────────────────────────────────
        disc_cp = StageCheckpoint(
            stage=StageName.DISCOVER,
            company_id=company,
            input_hash=hash_string(ticker),
            config_hash=hash_object({"form": "10-K"}),
            output_artifacts=[],
            status=StageStatus.COMPLETED,
            pipeline_version=pipeline_version,
            metadata={
                "filings": [
                    {
                        "accession_number": accession,
                        "cik": cik,
                        "form": "10-K",
                        "filing_date": "2024-11-15",
                        "report_date": "2024-09-30",
                        "primary_document": primary_doc,
                        "filing_url": filing_url,
                    }
                ]
            },
        )
        save_checkpoint(temp_dir, disc_cp)

        ext_cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id=company,
            input_hash=hash_object({"form": "10-K"}),
            config_hash=hash_object({"form": "10-K", "version": pipeline_version}),
            output_artifacts=[
                OutputArtifact(path=raw_path, hash=raw_sha256),
                OutputArtifact(path=sidecar_path, hash=compute_file_hash(sidecar_path)),
                OutputArtifact(path=text_path, hash=text_sha256),
            ],
            status=StageStatus.COMPLETED,
            pipeline_version=pipeline_version,
        )
        save_checkpoint(temp_dir, ext_cp)

        # ── Verifications ────────────────────────────────────────────
        assert raw_path.exists()
        assert sidecar_path.exists()
        assert text_path.exists()
        assert sections_path.exists()

        # Lineage: raw → bronze
        assert raw_sha256 != text_sha256

        # Checkpoint resume: discover
        disc_result = validate_checkpoint(
            temp_dir,
            company,
            StageName.DISCOVER,
            expected_input_hash=hash_string(ticker),
            expected_config_hash=hash_object({"form": "10-K"}),
            expected_version=pipeline_version,
        )
        assert disc_result.status == CheckpointStatus.VALID, (
            f"Discover checkpoint should be valid: {disc_result.message}"
        )

        # Checkpoint resume: extract
        ext_result = validate_checkpoint(
            temp_dir,
            company,
            StageName.EXTRACT,
            expected_input_hash=hash_object({"form": "10-K"}),
            expected_config_hash=hash_object({"form": "10-K", "version": pipeline_version}),
            expected_version=pipeline_version,
        )
        assert ext_result.status == CheckpointStatus.VALID, (
            f"Extract checkpoint should be valid: {ext_result.message}"
        )

        # Rerun with changed config → invalid
        ext_result_changed = validate_checkpoint(
            temp_dir,
            company,
            StageName.EXTRACT,
            expected_input_hash=hash_object({"form": "10-Q"}),
            expected_config_hash=hash_object({"form": "10-K", "version": pipeline_version}),
            expected_version=pipeline_version,
        )
        assert ext_result_changed.status != CheckpointStatus.VALID
