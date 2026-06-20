"""Release namespace scanner.

Recursive scanner that inspects all release artifacts for leaked identifiers:
- File/directory paths
- Filenames
- JSON keys and values
- CSV headers and cells
- Parquet columns, metadata, and string values
- ZIP member names
- Markdown links and URLs
- XML namespace strings
- HTML attributes
- Manifest references
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Patterns for detecting identifiers in paths and filenames
_CIK_PATTERN = re.compile(r"\b\d{7,10}\b")
_ACCESSION_DASHED = re.compile(r"\d{10}-\d{2}-\d{6}")
_ACCESSION_CLEAN = re.compile(r"\d{18}")
_TICKER_PATH_PATTERN = re.compile(r"[/\\]([A-Z]{1,5})(?:[/\\]|$)", re.IGNORECASE)
_URL_IDENTIFIER_PATTERN = re.compile(r"cik[=:/]\d+", re.IGNORECASE)
_XML_NAMESPACE_PATTERN = re.compile(r'xmlns[^=]*=["\']([^"\']*)["\']', re.IGNORECASE)
_XBRL_TAG_PATTERN = re.compile(r"<(ix:|xbrli:|link:|xlink:|us-gaap:)", re.IGNORECASE)
_HTML_CIK_ATTR = re.compile(r'EntityCentralIndexKey[^>]*?"?(\d+)"?', re.IGNORECASE)
_SCHEMA_REF_PATTERN = re.compile(r'xsi:schemaLocation=["\']([^"\']*)["\']', re.IGNORECASE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def scan_release_tree(
    root: Path,
    *,
    ticker: str = "",
    cik: str = "",
    known_identifiers: list[str] | None = None,
    scan_zip_paths: bool = True,
    scan_parquet: bool = True,
) -> dict[str, Any]:
    """Scan a release directory tree for leaked identifiers.

    Args:
        root: Root directory of the release tree.
        ticker: Company ticker to search for.
        cik: Company CIK to search for.
        known_identifiers: Additional private values to check for.
        scan_zip_paths: Whether to scan ZIP member names.
        scan_parquet: Whether to scan Parquet files.

    Returns:
        Dict with counts and findings.
    """
    private_values: set[str] = set()
    if ticker:
        private_values.add(ticker.upper())
        private_values.add(ticker.lower())
    if cik:
        private_values.add(cik.lstrip("0"))
        private_values.add(cik.zfill(10))
        private_values.add(cik)
    if known_identifiers:
        for val in known_identifiers:
            if val and len(val) > 2:
                private_values.add(val)
                private_values.add(val.upper())
                private_values.add(val.lower())

    findings: list[dict[str, Any]] = []
    counts = {
        "path_hits": 0,
        "filename_hits": 0,
        "json_hits": 0,
        "csv_hits": 0,
        "parquet_hits": 0,
        "zip_member_hits": 0,
        "markdown_hits": 0,
        "xml_namespace_hits": 0,
        "manifest_hits": 0,
        "total_hits": 0,
    }

    for fp in sorted(root.rglob("*")):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(root))

        # Path scan
        ph = _scan_path_for_identifiers(rel, private_values)
        if ph:
            counts["path_hits"] += 1
            findings.append({"type": "path", "path": rel, "hit_count": ph})

        # Filename scan
        fh = _scan_filename_for_accession(fp.name)
        if fh:
            counts["filename_hits"] += 1
            findings.append({"type": "filename", "path": rel})

        # Content-based scans
        suffix = fp.suffix.lower()
        if suffix == ".json":
            jh = _scan_json_for_identifiers(fp, private_values, ticker, cik)
            if jh:
                counts["json_hits"] += jh
                findings.append({"type": "json", "path": rel, "hit_count": jh})
        elif suffix == ".csv":
            ch = _scan_csv_for_identifiers(fp, private_values)
            if ch:
                counts["csv_hits"] += ch
                findings.append({"type": "csv", "path": rel, "hit_count": ch})
        elif suffix == ".parquet" and scan_parquet:
            pqh = _scan_parquet_for_identifiers(fp, private_values)
            if pqh:
                counts["parquet_hits"] += pqh
                findings.append({"type": "parquet", "path": rel, "hit_count": pqh})
        elif suffix == ".md":
            mh = _scan_markdown_for_identifiers(fp, private_values)
            if mh:
                counts["markdown_hits"] += mh
                findings.append({"type": "markdown", "path": rel, "hit_count": mh})
        elif suffix in (".html", ".htm", ".xml", ".xbrl"):
            xh = _scan_xml_for_identifiers(fp, private_values)
            if xh:
                counts["xml_namespace_hits"] += xh
                findings.append({"type": "xml_namespace", "path": rel, "hit_count": xh})

        # Manifest-specific scan
        if "manifest" in fp.name.lower():
            mfh = _scan_manifest_for_identifiers(fp, private_values, ticker)
            if mfh:
                counts["manifest_hits"] += mfh
                findings.append({"type": "manifest", "path": rel, "hit_count": mfh})

        # ZIP scan
        if scan_zip_paths and suffix == ".zip":
            zh = _scan_zip_members(fp, private_values)
            if zh:
                counts["zip_member_hits"] += zh
                findings.append({"type": "zip_members", "path": rel, "hit_count": zh})

    counts["total_hits"] = sum(
        counts[k] for k in counts if k != "total_hits"
    )
    return {
        "counts": counts,
        "findings": findings,
        "clean": counts["total_hits"] == 0,
    }


def _scan_path_for_identifiers(path_str: str, private_values: set[str]) -> int:
    """Scan a file/directory path for private identifiers. Returns hit count."""
    hits = 0
    for val in private_values:
        if len(val) < 3:
            continue
        if val in path_str:
            hits += 1
    # Check accession patterns
    if _ACCESSION_DASHED.search(path_str) or _ACCESSION_CLEAN.search(path_str):
        hits += 1
    return hits


def _scan_filename_for_accession(filename: str) -> bool:
    """Check if filename contains accession-like patterns."""
    if _ACCESSION_DASHED.search(filename):
        return True
    if _ACCESSION_CLEAN.search(filename):
        return True
    return False


def _scan_json_for_identifiers(
    fp: Path, private_values: set[str], ticker: str, cik: str
) -> int:
    """Scan JSON content (keys + values) for identifiers. Returns hit count."""
    hits = 0
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        text = json.dumps(data, sort_keys=True)

        # Scan raw text
        for val in private_values:
            if len(val) >= 3 and val in text:
                hits += 1

        # Recursively scan keys and string values
        def _scan(obj: Any) -> None:
            nonlocal hits
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _scan_str(str(k))
                    _scan(v)
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)
            elif isinstance(obj, str):
                _scan_str(obj)

        def _scan_str(s: str) -> None:
            nonlocal hits
            for val in private_values:
                if len(val) >= 3 and val in s:
                    hits += 1

        _scan(data)

    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return hits


def _scan_csv_for_identifiers(fp: Path, private_values: set[str]) -> int:
    """Scan CSV headers and cells for identifiers."""
    hits = 0
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
        for val in private_values:
            if len(val) >= 3 and val in content:
                hits += 1
    except (OSError, UnicodeDecodeError):
        pass
    return hits


def _scan_parquet_for_identifiers(fp: Path, private_values: set[str]) -> int:
    """Scan Parquet string columns and metadata for identifiers."""
    hits = 0
    try:
        import pandas as pd

        df = pd.read_parquet(fp)

        # Scan column names
        for col in df.columns:
            col_str = str(col)
            for val in private_values:
                if len(val) >= 3 and val in col_str:
                    hits += 1

        # Scan string column values (sample first 1000 rows per column)
        for col in df.select_dtypes(include=["object"]).columns:
            try:
                samples = df[col].dropna().head(1000).astype(str)
                for val in private_values:
                    if len(val) >= 4 and samples.str.contains(val, regex=False).any():
                        hits += 1
            except Exception:
                pass
    except Exception:
        pass
    return hits


def _scan_markdown_for_identifiers(fp: Path, private_values: set[str]) -> int:
    """Scan Markdown text and links for identifiers."""
    hits = 0
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")

        # Scan full text
        for val in private_values:
            if len(val) >= 3 and val in content:
                hits += 1

        # Scan URLs in markdown links
        for m in _MARKDOWN_LINK_PATTERN.finditer(content):
            url = m.group(2)
            for val in private_values:
                if len(val) >= 3 and val in url:
                    hits += 1
    except (OSError, UnicodeDecodeError):
        pass
    return hits


def _scan_xml_for_identifiers(fp: Path, private_values: set[str]) -> int:
    """Scan XML/HTML/XBRL for namespace strings, attributes, schema refs."""
    hits = 0
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")

        # XBRL tags
        if _XBRL_TAG_PATTERN.search(content):
            hits += 1

        # XML namespace URIs
        for m in _XML_NAMESPACE_PATTERN.finditer(content):
            ns = m.group(1)
            for val in private_values:
                if len(val) >= 3 and val in ns:
                    hits += 1

        # EntityCentralIndexKey attribute
        for m in _HTML_CIK_ATTR.finditer(content):
            cik_val = m.group(1)
            for val in private_values:
                if val.lstrip("0") == cik_val.lstrip("0"):
                    hits += 1

        # Schema references
        for m in _SCHEMA_REF_PATTERN.finditer(content):
            ref = m.group(1)
            for val in private_values:
                if len(val) >= 3 and val in ref:
                    hits += 1

        # URL identifiers (cik=)
        if _URL_IDENTIFIER_PATTERN.search(content):
            hits += 1

    except (OSError, UnicodeDecodeError):
        pass
    return hits


def _scan_manifest_for_identifiers(
    fp: Path, private_values: set[str], ticker: str
) -> int:
    """Scan manifest JSON for leaked company identifiers."""
    hits = 0
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")

        # Check for ticker in keys and values
        if ticker and ticker.upper() in content:
            hits += 1

        for val in private_values:
            if len(val) >= 4 and val in content:
                hits += 1

        # Check manifest-specific patterns
        parsed = json.loads(content)
        text = json.dumps(parsed, sort_keys=True)
        for val in private_values:
            if len(val) >= 4 and val in text:
                hits += 1

    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return hits


def _scan_zip_members(zip_path: Path, private_values: set[str]) -> int:
    """Scan ZIP member names for identifiers."""
    import zipfile

    hits = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                for val in private_values:
                    if len(val) >= 3 and val.lower() in name.lower():
                        hits += 1
    except (zipfile.BadZipFile, OSError):
        pass
    return hits
