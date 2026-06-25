"""Filing reconstruction attack QA.

Checks public reconstructed SEC markdown for leaked identifiers
and raw source content.
"""

from __future__ import annotations

import re
from typing import Any


class FilingReconstructionAttack:
    """Attack that checks public reconstructed SEC markdown for privacy leaks."""

    def __init__(self) -> None:
        self._patterns: dict[str, re.Pattern] = {
            "cik": re.compile(r"CIK|EntityCentralIndexKey|central.index.key", re.IGNORECASE),
            "accession": re.compile(r"\d{10}\-\d{2}\-\d{6}|ACCESSION NUMBER", re.IGNORECASE),
            "sec_file_number": re.compile(r"\d{2}\-\d{6,8}|Commission File Number", re.IGNORECASE),
            "xbrl_namespace": re.compile(r"xbrl[a-z]*:|dei:|us-gaap[a-z]*:|ix:", re.IGNORECASE),
            "sec_gov_url": re.compile(r"sec\.gov", re.IGNORECASE),
            "raw_file_ext": re.compile(r"\.html|\.xml|\.xbrl"),
            "form_header": re.compile(
                r"FORM\s+10[- ][KQ]|FORM\s+8[- ]K|FORM\s+DEF14A", re.IGNORECASE
            ),
        }

    def run(
        self,
        company_id: str,
        reconstructed_sections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run the filing reconstruction attack.

        Args:
            company_id: The anonymized company ID.
            reconstructed_sections: List of reconstructed section dicts with 'content' key.

        Returns:
            Dict with findings and violations.
        """
        violations: list[str] = []
        for section in reconstructed_sections:
            content = section.get("content", "")
            for check_name, pattern in self._patterns.items():
                matches = pattern.findall(content)
                if matches:
                    violations.append(
                        f"Found {len(matches)} instance(s) of {check_name} in section"
                    )

        return {
            "company_id": company_id,
            "violations": violations,
            "passes": len(violations) == 0,
            "num_sections_checked": len(reconstructed_sections),
        }


def check_public_sec_directory(sec_dir: str) -> dict[str, Any]:
    """Check public SEC output directory for banned files and content."""
    import os

    violations: list[str] = []
    files_checked = 0

    if not os.path.isdir(sec_dir):
        return {"files_checked": 0, "violations": ["Directory not found"], "passes": False}

    banned_text = [
        "CIK",
        "EntityCentralIndexKey",
        "ACCESSION NUMBER",
        "xbrl",
        "dei:",
        "us-gaap",
        "ix:",
        "sec.gov",
    ]

    for root, _, files in os.walk(sec_dir):
        for fname in files:
            # Check for forbidden file types
            if fname.endswith((".html", ".xml", ".xbrl")):
                violations.append(f"Forbidden file type in public SEC dir: {fname}")
                continue
            if not fname.endswith((".md", ".csv", ".json")):
                continue

            try:
                data = open(os.path.join(root, fname), errors="ignore").read().lower()
                files_checked += 1
                for ban in banned_text:
                    if ban.lower() in data:
                        violations.append(f"Found banned text '{ban}' in {fname}")
            except OSError:
                continue

    return {
        "files_checked": files_checked,
        "violations": violations,
        "passes": len(violations) == 0,
    }
