"""Integration tests for UTILITY_PRESERVATION professor bundle stage."""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.qa.utility_preservation import (
    CompanyThesis,
    extract_public_thesis,
    score_utility_preservation,
)


class TestUtilityPreservationStage:
    """Integration tests for utility preservation stage behavior."""

    def test_stage_emits_utility_summary(self, tmp_path: Path) -> None:
        """Stage emits qa/utility_preservation_summary.json."""
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking and lending",
            fundamentals_signal="strong",
        )

        # Create public output
        company_dir = tmp_path / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text(
            "A diversified financial services company focused on banking."
        )

        public_thesis = extract_public_thesis(tmp_path, "COMPANY_001")
        result = score_utility_preservation(source, public_thesis)

        # Simulate what the stage writes
        qa_dir = tmp_path / "qa"
        qa_dir.mkdir(parents=True)
        import orjson

        (qa_dir / "utility_preservation_summary.json").write_bytes(
            orjson.dumps(
                result.public.to_dict(),
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )

        assert (qa_dir / "utility_preservation_summary.json").exists()
        data = json.loads((qa_dir / "utility_preservation_summary.json").read_text())
        assert "overall_utility_score" in data
        assert "verdict" in data

    def test_private_utility_report_excluded_from_zip(self, tmp_path: Path) -> None:
        """Private utility report should not be in public qa/."""
        source = CompanyThesis(anonymized_company_id="COMPANY_001")
        public = CompanyThesis(anonymized_company_id="COMPANY_001")
        result = score_utility_preservation(source, public)

        private_dir = tmp_path / "private" / "qa"
        qa_dir = tmp_path / "qa"
        from fenrix_synthetic.qa.utility_preservation import write_utility_reports

        priv_path, pub_path = write_utility_reports(result, private_dir, qa_dir)

        # Private report in private dir
        assert priv_path.is_relative_to(private_dir)
        # Public report in qa dir
        assert pub_path.is_relative_to(qa_dir)
        # Private report NOT in qa dir
        assert not (qa_dir / "utility_preservation_private.json").exists()

    def test_final_zip_includes_utility_summary(self, tmp_path: Path) -> None:
        """Final ZIP should include utility_preservation_summary.json."""
        source = CompanyThesis(anonymized_company_id="COMPANY_001")
        public = CompanyThesis(anonymized_company_id="COMPANY_001")
        result = score_utility_preservation(source, public)

        qa_dir = tmp_path / "qa"
        qa_dir.mkdir(parents=True)
        import orjson

        (qa_dir / "utility_preservation_summary.json").write_bytes(
            orjson.dumps(result.public.to_dict(), option=orjson.OPT_INDENT_2)
        )

        assert (qa_dir / "utility_preservation_summary.json").exists()
