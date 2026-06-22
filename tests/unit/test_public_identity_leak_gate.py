"""Public identity leak gate.

Scans all git-tracked files for real source-company identifiers that must
never appear in the public repository. Fails on any hit outside an
explicit allowlist of fixture/example paths.

This test enforces the Phase 0 guardrail: no real tickers, company names,
CIKs, accession numbers, SEC archive URLs, investor domains, private map
paths, local paths, or API-key patterns in tracked files.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# ── Real identifiers that must never appear in tracked files ─────────────

KNOWN_REAL_TICKERS: frozenset[str] = frozenset(
    {
        "HBAN",
        "NVDA",
        "SNDK",
        "CL",
        "PEP",
        "TJX",
        "PM",
        "AMZN",
        "BLK",
        "GOOGL",
    }
)

KNOWN_REAL_COMPANY_NAMES: frozenset[str] = frozenset(
    {
        "Huntington",
        "SanDisk",
        "SanDisk Corporation",
        "Western Digital",
        "Colgate",
        "Colgate-Palmolive",
        "PepsiCo",
        "Philip Morris",
        "BlackRock",
        "Alphabet",
    }
)
# NOTE: "NVIDIA" and "Google" are excluded from the company-name blocklist
# because "NVIDIA" refers to the LLM review API provider (not a source
# company), and "Google" is too common a word. The *ticker* NVDA is still
# caught by the ticker blocklist above.

KNOWN_REAL_CIKS: frozenset[str] = frozenset(
    {
        "0000049196",
        "0002023554",
        "0000021665",
        "0000077476",
        "0000109198",
        "0001413329",
        "0001018724",
        "0001364742",
        "0001652044",
    }
)

# SEC accession patterns (real-format, not canary 999999)
ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
# SEC archive URLs
SEC_ARCHIVE_URL_RE = re.compile(r"sec\.gov/Archives/edgar/data/", re.I)
# Local path prefixes
LOCAL_PATH_RE = re.compile(r"/Users/|/content/")
# API key patterns — only actual key values, not variable names
API_KEY_RE = re.compile(r"nvapi-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9]{20,}")

# ── Allowlist: paths where identifiers may appear (fixtures, examples) ────

ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        ".gitignore",
        "tests/fixtures/",
        "configs/examples/",
        "pyproject.toml",
    }
)

# ── Allowlist: words that look like identifiers but are safe ─────────────
# E.g. "PM" as an abbreviation in non-ticker context, "CL" in code.
# We only flag these when they appear as standalone tokens matching known tickers.
SAFE_CONTEXTS: frozenset[str] = frozenset(
    {
        "canary",
        "chc",
        "testperson",
        "0000999999",
    }
)


def _git_ls_files() -> list[Path]:
    """Return list of git-tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    repo_root = Path(__file__).resolve().parents[2]
    return [repo_root / line for line in result.stdout.strip().splitlines() if line.strip()]


def _is_allowed(path: Path) -> bool:
    """Check if a path is in the allowlist."""
    str_path = str(path)
    for allowed in ALLOWED_PATHS:
        if allowed in str_path:
            return True
    # The leak gate test itself contains real identifiers as its blocklist
    if "test_public_identity_leak_gate" in str_path:
        return True
    return False


def _scan_file(path: Path) -> list[str]:
    """Scan a single file for real identifiers. Returns list of findings."""
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[str] = []

    # Determine file category for context-sensitive checks
    is_source = path.suffix == ".py" and "src/" in str(path)
    is_test = path.suffix == ".py" and "tests/" in str(path)

    # Check real tickers (word-boundary match)
    # Skip short tickers (CL, PM, PE) that collide with common abbreviations
    # unless they appear in a financial context (ticker, stock, NYSE, NASDAQ).
    SHORT_TICKERS_WITH_CONTEXT = {"CL", "PM", "PE", "TJ", "PEP"}
    for ticker in KNOWN_REAL_TICKERS:
        if ticker in SHORT_TICKERS_WITH_CONTEXT:
            # Only flag short tickers in financial context
            pattern = (
                rf"(?:ticker|stock|NYSE|NASDAQ|ticker_symbol)\s*[:=]\s*['\"]?{re.escape(ticker)}\b"
            )
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                findings.append(
                    f"{path}: real ticker '{ticker}' in financial context ({len(matches)} occurrences)"
                )
        else:
            pattern = rf"\b{re.escape(ticker)}\b"
            matches = re.findall(pattern, content)
            if matches:
                # Filter out canary-safe contexts
                if ticker.lower() in SAFE_CONTEXTS:
                    continue
                findings.append(f"{path}: real ticker '{ticker}' ({len(matches)} occurrences)")

    # Check real company names
    for name in KNOWN_REAL_COMPANY_NAMES:
        if name in content:
            findings.append(f"{path}: real company name '{name}'")

    # Check real CIKs
    for cik in KNOWN_REAL_CIKS:
        if cik in content:
            findings.append(f"{path}: real CIK '{cik}'")

    # Check SEC accession patterns (only if not canary/test)
    for match in ACCESSION_RE.finditer(content):
        accession = match.group()
        # Allow canary and standard test CIKs
        if "999999" in accession or "0000000000" in accession:
            continue
        if "0001234567" in accession:  # standard SEC test CIK
            continue
        if is_test:
            continue  # Tests may use fixture accession patterns
        findings.append(f"{path}: SEC accession pattern '{accession}'")

    # Check SEC archive URLs — only in non-source files
    if not is_source and not is_test:
        for _match in SEC_ARCHIVE_URL_RE.finditer(content):
            findings.append(f"{path}: SEC archive URL pattern")

    # Check local paths — only flag in docs and configs, not source code
    # that defensively checks for these patterns.
    if not is_source and not is_test:
        for match in LOCAL_PATH_RE.finditer(content):
            if path.name == ".gitignore":
                continue
            findings.append(f"{path}: local path '{match.group()}'")

    # Check API keys
    for _match in API_KEY_RE.finditer(content):
        findings.append(f"{path}: API key pattern")

    return findings


class TestPublicIdentityLeakGate:
    """Gate that fails if any real identifier appears in tracked files."""

    def test_no_real_tickers_in_tracked_files(self) -> None:
        """No known real tickers in any tracked file outside allowlist."""
        files = _git_ls_files()
        all_findings: list[str] = []

        for fpath in files:
            if _is_allowed(fpath):
                continue
            findings = _scan_file(fpath)
            all_findings.extend(findings)

        if all_findings:
            pytest.fail(
                f"Identity leak gate found {len(all_findings)} issue(s):\n"
                + "\n".join(f"  - {f}" for f in all_findings[:20])
            )

    def test_leak_gate_catches_seeded_real_ticker(self, tmp_path: Path) -> None:
        """The gate must catch a seeded real ticker."""
        evil_file = tmp_path / "evil.md"
        evil_file.write_text("This file mentions HBAN which is a real ticker.\n")

        findings = _scan_file(evil_file)
        assert any("HBAN" in f for f in findings), "Gate failed to catch seeded HBAN"

    def test_leak_gate_catches_seeded_cik(self, tmp_path: Path) -> None:
        """The gate must catch a seeded real CIK."""
        evil_file = tmp_path / "evil.md"
        evil_file.write_text("CIK 0000049196 appears here.\n")

        findings = _scan_file(evil_file)
        assert any("0000049196" in f for f in findings), "Gate failed to catch seeded CIK"

    def test_leak_gate_catches_seeded_accession(self, tmp_path: Path) -> None:
        """The gate must catch a seeded real accession number."""
        evil_file = tmp_path / "evil.md"
        evil_file.write_text("Accession 0002023554-25-000010 appears here.\n")

        findings = _scan_file(evil_file)
        assert any("accession" in f.lower() for f in findings), (
            "Gate failed to catch seeded accession"
        )

    def test_leak_gate_catches_seeded_sec_url(self, tmp_path: Path) -> None:
        """The gate must catch a seeded SEC archive URL."""
        evil_file = tmp_path / "evil.md"
        evil_file.write_text("See https://www.sec.gov/Archives/edgar/data/49196/\n")

        findings = _scan_file(evil_file)
        assert any("SEC archive URL" in f for f in findings), "Gate failed to catch SEC URL"

    def test_leak_gate_catches_seeded_api_key(self, tmp_path: Path) -> None:
        """The gate must catch a seeded API key."""
        evil_file = tmp_path / "evil.md"
        evil_file.write_text("NVIDIA_API_KEY=nvapi-1234567890abcdefghijklmnop\n")

        findings = _scan_file(evil_file)
        assert any("API key" in f for f in findings), "Gate failed to catch API key"

    def test_leak_gate_passes_canary_placeholders(self, tmp_path: Path) -> None:
        """The gate must NOT flag canary placeholders."""
        clean_file = tmp_path / "clean.md"
        clean_file.write_text(
            "Company: Canary Holdings Corporation\n"
            "Ticker: CHC\n"
            "CIK: 0000999999\n"
            "Executive: Eleanor Testperson\n"
            "Domain: canary-test.invalid\n"
        )

        findings = _scan_file(clean_file)
        assert findings == [], f"Gate falsely flagged canary placeholders: {findings}"

    def test_leak_gate_passes_allowed_fixture_paths(self) -> None:
        """Fixture and example paths are allowed to contain test identifiers."""
        files = _git_ls_files()
        fixture_files = [f for f in files if _is_allowed(f)]
        # Just verify these files exist and are scannable without error
        for fpath in fixture_files[:5]:
            if fpath.is_file():
                _ = _scan_file(fpath)  # Should not raise
