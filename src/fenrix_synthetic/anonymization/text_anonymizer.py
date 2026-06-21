"""Text anonymizer for SEC filings and news articles.

Handles both flat text and XML/XBRL structure awareness.
Anonymizes text nodes, XBRL attributes, contexts, and identifiers.

New: Converts Inline-XBRL/HTML SEC filings to readable Markdown before
anonymization, then anonymizes the Markdown representation.
The output is genuine .md files, not renamed HTML.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, cast

from ..identity import EntityRegistry
from ..masking import DeterministicMasker
from ..release.pseudonym_paths import build_pseudonym_path_map
from ..storage.hashing import hash_file
from .registry_load import (
    RegistryLoadSummary,
    load_atlas,
)

logger = logging.getLogger(__name__)


class TextAnonymizer:
    """Anonymize text and HTML documents using deterministic masking."""

    def __init__(
        self,
        ticker: str,
        originals_dir: Path,
        anonymized_dir: Path,
        private_maps_dir: Path,
        suffix: str = "",
    ) -> None:
        self.ticker = ticker.upper()
        self.originals_dir = originals_dir
        self.anonymized_dir = anonymized_dir
        self.private_maps_dir = private_maps_dir
        self.suffix = suffix

    def anonymize_all(
        self,
        selected_paths: list[Path] | None = None,
        *,
        preloaded_registry: EntityRegistry | None = None,
        preloaded_summary: RegistryLoadSummary | None = None,
    ) -> list[dict[str, Any]]:
        """Anonymize filings for ``<ticker>``.

        Parameters
        ----------
        selected_paths:
            Optional explicit list of HTML filings to process. When
            ``None`` (the legacy default), every ``*.html`` and
            ``*.htm`` file under ``originals_dir / sec / filings`` is
            processed — preserving backwards compatibility for callers
            that already enumerate the directory themselves. When a
            non-empty list is supplied, ONLY those paths are processed,
            in the order given.

            The orchestrator passes this argument so that a
            ``--limit-forms`` directive actually restricts work, instead
            of being reported but ignored.

        preloaded_registry:
            Optional already-loaded ``EntityRegistry``. When supplied,
            the YAML disk load is skipped — the orchestrator can call
            ``registry_load.load_atlas`` once during Phase 1.5 and
            hand the loaded registry to every anonymizer call to
            avoid duplicate disk reads AND enforce the per-run
            ``aliases_loaded == 0`` fail-closed contract before any
            public surrogate is written.

        preloaded_summary:
            Optional ``RegistryLoadSummary`` matching ``preloaded_registry``
            so the QA ``registry_load_report.json`` reflects the actual
            load attempt (not a placeholder).
        """
        manifests: list[dict[str, Any]] = []
        filings_dir = (
            self.originals_dir / "sec" / "filings"
        )  # Resolve the candidate list up-front so failures (missing
        # directory, empty selection) fail closed with zero work done.
        # SEC filings arrive as both ``*.html`` and ``*.htm`` (the latter
        # is the historical SEC convention) — accept both extensions so
        # real filings aren't silently dropped.
        accepted_suffixes = (".html", ".htm")

        def _is_filing(p: Path) -> bool:
            return p.suffix.lower() in accepted_suffixes

        if selected_paths is not None:
            html_paths: list[Path] = [p for p in selected_paths if _is_filing(p)]
            if not html_paths:
                logger.info(
                    "anonymize_all: no .html/.htm paths in selected_paths for %s",
                    self.ticker,
                )
                return manifests
        else:
            if not filings_dir.exists():
                return manifests
            html_paths = sorted(list(filings_dir.glob("*.html")) + list(filings_dir.glob("*.htm")))
            if not html_paths:
                logger.warning("anonymize_all: no .html/.htm filings found under %s", filings_dir)
                return manifests

        atlas_path = self.private_maps_dir / "identity_atlas.yaml"

        # Resolve the registry via three paths in priority order:
        # 1) ``preloaded_registry`` (orchestrator - Phase 1.5)
        # 2) cached registry from a previous call this run
        # 3) disk YAML load via ``registry_load.load_atlas``
        if preloaded_registry is not None:
            reg = preloaded_registry
            self.last_load_summary = preloaded_summary or RegistryLoadSummary(
                atlas_path=atlas_path,
                redacted_ticker=self.ticker,
            )
            self._cached_reg = reg
            self._cached_load_summary = self.last_load_summary
        else:
            # Cache-hit branch: ``_resume_cached_registry`` centralises
            # the cast / isinstance / summary narrowing so the contract
            # lives in ONE place. The bare ``or`` short-circuit and the
            # getattr-Any narrowing pitfalls are gone.
            cached = self._resume_cached_registry(atlas_path)
            if cached is not None:
                reg, self.last_load_summary = cached
            else:
                # ``load_atlas`` returns ``tuple[EntityRegistry | None, ...]``,
                # so we hand the loaded value to ``reg_raw`` (a
                # ``EntityRegistry | None`` variable) and re-bind ``reg``
                # through an explicit ``cast`` after the None check.
                # This is the canonical mypy pattern when the union
                # pattern appears in the return signature of an external
                # function.
                reg_raw, load_summary = load_atlas(atlas_path, ticker=self.ticker)
                self.last_load_summary = load_summary
                if reg_raw is None:
                    logger.warning(
                        "No identity atlas loaded for %s (status=%s)",
                        self.ticker,
                        load_summary.status,
                    )
                    return manifests
                reg = cast(EntityRegistry, reg_raw)
                self._cached_reg = reg
                self._cached_load_summary = load_summary

        masker = DeterministicMasker(reg)
        config_hash = reg.config_hash()

        # Extract CIK and accessions for XBRL-aware processing
        cik = self._extract_cik_from_registry(reg)
        accessions = self._extract_accessions_from_registry(reg)
        path_map = build_pseudonym_path_map(self.ticker, cik, accessions)

        for html_path in html_paths:
            try:
                raw_html = html_path.read_text(encoding="utf-8", errors="replace")
                artifact_id = html_path.stem

                # Step 0: Convert HTML/Inline-XBRL to readable Markdown
                from ..extraction.converter import HtmlFilingExtractor

                extractor = HtmlFilingExtractor()
                extraction = extractor.extract(
                    raw_html,
                    metadata={"source": str(html_path.name)},
                )
                text = extraction["text"]

                # Phase A: XBRL-aware structural anonymization
                text = self._anonymize_xbrl_structure(text, cik, path_map)

                # Phase B: Deterministic regex masking (flat text)
                masked_text, _sanitized_meta, audit, summary = masker.mask_and_sanitize_metadata(
                    text,
                    {"source": str(html_path.name), "registry_id": reg.metadata.registry_id},
                    config_hash,
                )

                # Get pseudonym filename (never leak accession)
                import hashlib

                public_filename = path_map.public_filename(artifact_id)
                if not public_filename.endswith(".md"):
                    # Fallback: hash-based filename, never the raw accession
                    fallback_hash = hashlib.sha256(artifact_id.encode()).hexdigest()[:12]
                    public_filename = f"filing_{fallback_hash}.md"

                # Save anonymized
                out_path = self.anonymized_dir / "sec" / public_filename
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(masked_text, encoding="utf-8")

                manifests.append(
                    {
                        "artifact_id": f"{self.ticker}_anon_{artifact_id}",
                        "source": "deterministic_masker",
                        "original_path": str(html_path.relative_to(self.originals_dir.parent)),
                        "anonymized_path": str(out_path.relative_to(self.anonymized_dir.parent)),
                        "sha256": hash_file(out_path),
                        "match_count": summary.match_count,
                        "replacement_count": summary.replacement_count,
                        "config_hash": config_hash,
                    }
                )
            except Exception as exc:
                logger.warning("Anonymization failed for %s: %s", html_path, exc)

        return manifests

    def anonymize_news(self) -> list[dict[str, Any]]:
        """Anonymize news articles."""
        manifests: list[dict[str, Any]] = []
        news_path = self.originals_dir / "news" / "articles.json"
        if not news_path.exists():
            return manifests

        import orjson

        articles = orjson.loads(news_path.read_bytes())

        # Load atlas
        atlas_path = self.private_maps_dir / "identity_atlas.yaml"
        if not atlas_path.exists():
            logger.warning("No identity atlas found for %s", self.ticker)
            return manifests

        # Cache-hit: defer to ``_resume_cached_registry`` so the cache
        # contract lives in ONE place — fixes the post-cast duplication
        # between ``anonymize_all`` and ``anonymize_news``. Cache miss
        # below also seeds ``self._cached_reg`` / ``self._cached_load_summary``
        # so the next call (news, second HTML pass) hits cache.
        cached = self._resume_cached_registry(atlas_path)
        if cached is not None:
            reg, self.last_load_summary = cached
        else:
            reg_raw, load_summary = load_atlas(atlas_path, ticker=self.ticker)
            self.last_load_summary = load_summary
            if reg_raw is None:
                logger.warning(
                    "No identity atlas loaded for %s (status=%s) in news path",
                    self.ticker,
                    load_summary.status,
                )
                return manifests
            reg = cast(EntityRegistry, reg_raw)
            self._cached_reg = reg
            self._cached_load_summary = load_summary

        masker = DeterministicMasker(reg)
        config_hash = reg.config_hash()

        anonymized_articles: list[dict[str, Any]] = []
        for article in articles:
            try:
                text = article.get("body", "") or article.get("summary", "")
                if not text:
                    anonymized_articles.append(article)
                    continue

                masked_text, _sanitized_meta, _audit, _summary = masker.mask_and_sanitize_metadata(
                    text,
                    {"source": "news", "registry_id": reg.metadata.registry_id},
                    config_hash,
                )
                anon_article = dict(article)
                anon_article["body"] = masked_text
                anon_article["summary"] = masked_text[:500]
                anonymized_articles.append(anon_article)
            except Exception as exc:
                logger.warning("News anonymization failed: %s", exc)
                anonymized_articles.append(article)

        out_path = self.anonymized_dir / "news" / "articles.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(
            orjson.dumps(anonymized_articles, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        manifests.append(
            {
                "artifact_id": f"{self.ticker}_anon_news",
                "source": "deterministic_masker",
                "anonymized_path": str(out_path.relative_to(self.anonymized_dir.parent)),
                "sha256": hash_file(out_path),
                "article_count": len(anonymized_articles),
                "config_hash": config_hash,
            }
        )

        return manifests

    def _anonymize_xbrl_structure(self, text: str, cik: str, path_map: Any) -> str:
        """Apply XBRL/XML-aware structural anonymization before regex masking.

        Targets:
        - EntityCentralIndexKey attributes in XBRL contexts
        - CIK values in XBRL facts and context IDs
        - XBRL namespace URIs containing CIKs
        - Schema reference URLs with company identifiers
        - Accession numbers in XBRL contexts
        - Identifier schemes referencing CIKs
        """
        if not cik:
            return text

        import hashlib

        clean_cik = cik.lstrip("0")
        padded_cik = cik.zfill(10)
        cik_pseudo = f"CIK_{hashlib.sha256(cik.encode()).hexdigest()[:12]}"

        # 1. EntityCentralIndexKey attribute in XML
        text = re.sub(
            r'(<[^>]*?EntityCentralIndexKey[^>]*?")(\d+)("[^>]*?>)',
            lambda m: m.group(1) + cik_pseudo + m.group(3),
            text,
            flags=re.IGNORECASE,
        )

        # 2. Bare CIK in XML context identifiers
        text = re.sub(
            rf'(contextRef|cik)\s*=\s*"?{re.escape(clean_cik)}"?',
            f'\\1="{cik_pseudo}"',
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            rf'(contextRef|cik)\s*=\s*"?{re.escape(padded_cik)}"?',
            f'\\1="{cik_pseudo}"',
            text,
            flags=re.IGNORECASE,
        )

        # 3. CIK in URLs
        text = re.sub(
            rf"cik={re.escape(clean_cik)}",
            f"cik={cik_pseudo}",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            rf"cik={re.escape(padded_cik)}",
            f"cik={cik_pseudo}",
            text,
            flags=re.IGNORECASE,
        )

        # 4. CIK in identifier elements (XBRL)
        text = re.sub(
            rf"(<identifier[^>]*>)\s*{re.escape(clean_cik)}\s*(</identifier>)",
            rf"\1 {cik_pseudo} \2",
            text,
            flags=re.IGNORECASE,
        )

        # 5. Namespace URIs containing CIK patterns
        text = re.sub(
            rf"/{re.escape(clean_cik)}/",
            f"/{cik_pseudo}/",
            text,
        )
        text = re.sub(
            rf"/{re.escape(padded_cik)}/",
            f"/{cik_pseudo}/",
            text,
        )

        # 6. Accession numbers in XBRL contexts (dashed and non-dashed forms)
        if path_map and hasattr(path_map, "accession_pseudonyms"):
            for acc, pseudo in path_map.accession_pseudonyms.items():
                clean_acc = acc.replace("-", "")
                # Only replace if > 12 chars (to avoid false positives)
                if len(acc) > 12:
                    text = text.replace(acc, pseudo)
                if len(clean_acc) > 12:
                    text = text.replace(clean_acc, pseudo)

        return text

    def _extract_cik_from_registry(self, reg: EntityRegistry) -> str:
        """Extract CIK from registry entities."""
        for entity in reg.all_entities():
            if entity.entity_type.value == "cik":
                return entity.canonical_private_value
        return ""

    def _extract_accessions_from_registry(self, reg: EntityRegistry) -> list[str]:
        """Extract accession numbers from registry entities."""
        accessions: list[str] = []
        for entity in reg.all_entities():
            if entity.entity_type.value == "sec_accession_number":
                accessions.append(entity.canonical_private_value)
        return accessions

    # NOTE: the legacy ``_load_registry`` method was retired by this
    # refactor. Registry loading now goes through ``registry_load.load_atlas``,
    # which enforces strict normalization, fail-closed behaviour on zero
    # aliases, and surfaces a ``RegistryLoadSummary`` for QA. The orchestrator
    # calls ``load_atlas`` once during Phase 1.5 and passes the loaded
    # registry into ``anonymize_all(preloaded_registry=...)`` to enforce the
    # ``aliases_loaded == 0`` fail-closed contract BEFORE any public
    # surrogate is written.

    def _resume_cached_registry(
        self, atlas_path: Path
    ) -> tuple[EntityRegistry, RegistryLoadSummary] | None:
        """Return the cached registry + summary if a prior call seeded them.

        Centralising the cache-hit logic in ONE place keeps the
        ``anonymize_all`` (HTML filings) and ``anonymize_news`` paths
        aligned AND lets ``cast`` + ``isinstance`` narrow cleanly for
        mypy in a single scope instead of two.

        - ``getattr`` returns ``Any``, so a bare ``is not None`` would
          not narrow. ``cast(EntityRegistry | None, ...)`` makes the
          type honest under strict flags.
        - The ``isinstance`` guard then narrows the union to the
          ``EntityRegistry`` arm without ``assert`` (which evaporates
          under ``python -O``).
        - When the path differs from the cached ``atlas_path`` we still
          return the cached registry but with a fresh
          ``RegistryLoadSummary`` so callers' downstream QA carries the
          right path.
        """
        cached_summary = getattr(self, "_cached_load_summary", None)
        cached_reg = cast(EntityRegistry | None, getattr(self, "_cached_reg", None))
        if isinstance(cached_reg, EntityRegistry):
            if cached_summary is not None and cached_summary.atlas_path == atlas_path:
                summary: RegistryLoadSummary = cached_summary
            else:
                summary = RegistryLoadSummary(
                    atlas_path=atlas_path,
                    redacted_ticker=self.ticker,
                )
            return cached_reg, summary
        return None
