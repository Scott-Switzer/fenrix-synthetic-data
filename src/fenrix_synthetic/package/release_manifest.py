"""Release manifest for V3 bundles.

Required manifest fields documenting what is and is not in the release.
Rules:
- identity_map_included must be false
- raw_source_included must be false
- raw_sec_html_included must be false
- raw_xbrl_included must be false
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ReleaseManifest(BaseModel):
    """Release manifest documenting the contents and provenance of a V3 bundle."""

    release_id: str = Field(description="Unique release identifier")
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO timestamp of creation",
    )
    repo_sha: str = Field(default="", description="Git commit SHA at build time")
    branch: str = Field(default="", description="Git branch at build time")
    pipeline_version: str = Field(default="0.1.0", description="Pipeline version")
    config_hash: str = Field(default="", description="SHA-256 hash of build configuration")
    random_seed: str = Field(default="", description="Random seed used for reproducibility")

    source_count: int = Field(default=0, description="Number of source companies")
    public_company_ids: list[str] = Field(
        default_factory=list, description="Public company identifiers in release"
    )
    artifact_counts: dict[str, int] = Field(
        default_factory=dict, description="Count of artifacts by type"
    )
    qa_reports: list[str] = Field(
        default_factory=list, description="Paths to QA reports relative to bundle root"
    )

    # Privacy flags - these MUST be false
    identity_map_included: bool = Field(
        default=False, description="MUST be false - no identity mapping in release"
    )
    raw_source_included: bool = Field(
        default=False, description="MUST be false - no raw source data in release"
    )
    raw_sec_html_included: bool = Field(
        default=False, description="MUST be false - no raw SEC HTML in release"
    )
    raw_xbrl_included: bool = Field(
        default=False, description="MUST be false - no raw XBRL in release"
    )

    strict_release_gate: bool = Field(
        default=True, description="Whether strict release gate was applied"
    )
    excluded_private_artifacts: list[str] = Field(
        default_factory=list, description="Private artifacts excluded from release"
    )
    known_limitations: list[str] = Field(
        default_factory=list, description="Known limitations of this release"
    )

    @model_validator(mode="after")
    def _validate_privacy_flags(self) -> ReleaseManifest:
        """All privacy inclusion flags must be False."""
        errors: list[str] = []
        if self.identity_map_included:
            errors.append("identity_map_included must be False")
        if self.raw_source_included:
            errors.append("raw_source_included must be False")
        if self.raw_sec_html_included:
            errors.append("raw_sec_html_included must be False")
        if self.raw_xbrl_included:
            errors.append("raw_xbrl_included must be False")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    def to_json(self) -> str:
        import orjson

        return orjson.dumps(
            self.model_dump(),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
        ).decode("utf-8")

    def to_markdown(self) -> str:
        """Generate a markdown version of the manifest."""
        lines = [
            f"# Release Manifest: {self.release_id}",
            "",
            f"- **Release ID:** {self.release_id}",
            f"- **Created:** {self.created_at}",
            f"- **Repo SHA:** {self.repo_sha}",
            f"- **Branch:** {self.branch}",
            f"- **Pipeline Version:** {self.pipeline_version}",
            f"- **Config Hash:** {self.config_hash}",
            f"- **Random Seed:** {self.random_seed}",
            "",
            "## Contents",
            f"- **Source Count:** {self.source_count}",
            f"- **Public Company IDs:** {', '.join(self.public_company_ids) or 'none'}",
            "",
            "### Artifact Counts",
        ]
        for atype, count in sorted(self.artifact_counts.items()):
            lines.append(f"- {atype}: {count}")

        lines.extend(
            [
                "",
                "### QA Reports",
            ]
        )
        for report in self.qa_reports:
            lines.append(f"- {report}")

        lines.extend(
            [
                "",
                "## Privacy Guarantees",
                f"- **Identity Map Included:** {self.identity_map_included}",
                f"- **Raw Source Included:** {self.raw_source_included}",
                f"- **Raw SEC HTML Included:** {self.raw_sec_html_included}",
                f"- **Raw XBRL Included:** {self.raw_xbrl_included}",
                f"- **Strict Release Gate:** {self.strict_release_gate}",
                "",
                "### Excluded Private Artifacts",
            ]
        )
        for artifact in self.excluded_private_artifacts:
            lines.append(f"- {artifact}")

        lines.extend(
            [
                "",
                "### Known Limitations",
            ]
        )
        for limit in self.known_limitations:
            lines.append(f"- {limit}")

        return "\n".join(lines) + "\n"


def create_release_manifest(
    release_id: str,
    *,
    repo_sha: str = "",
    branch: str = "",
    pipeline_version: str = "0.1.0",
    config_hash: str = "",
    random_seed: str = "",
    source_count: int = 0,
    public_company_ids: list[str] | None = None,
    artifact_counts: dict[str, int] | None = None,
    qa_reports: list[str] | None = None,
    excluded_private_artifacts: list[str] | None = None,
    known_limitations: list[str] | None = None,
) -> ReleaseManifest:
    """Create a validated release manifest.

    All privacy flags are enforced as False.
    """
    return ReleaseManifest(
        release_id=release_id,
        repo_sha=repo_sha,
        branch=branch,
        pipeline_version=pipeline_version,
        config_hash=config_hash,
        random_seed=random_seed,
        source_count=source_count,
        public_company_ids=public_company_ids or [],
        artifact_counts=artifact_counts or {},
        qa_reports=qa_reports or [],
        identity_map_included=False,
        raw_source_included=False,
        raw_sec_html_included=False,
        raw_xbrl_included=False,
        strict_release_gate=True,
        excluded_private_artifacts=excluded_private_artifacts or [],
        known_limitations=known_limitations or [],
    )
