"""Text anonymizer for SEC filings and news articles."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..identity import EntityRegistry
from ..masking import DeterministicMasker
from ..storage.hashing import hash_file

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

    def anonymize_all(self) -> list[dict[str, Any]]:
        """Anonymize all text/HTML files in originals/sec/filings."""
        manifests: list[dict[str, Any]] = []
        filings_dir = self.originals_dir / "sec" / "filings"
        if not filings_dir.exists():
            return manifests

        # Load atlas
        atlas_path = self.private_maps_dir / "identity_atlas.yaml"
        if not atlas_path.exists():
            logger.warning("No identity atlas found for %s", self.ticker)
            return manifests

        import yaml

        atlas_data = yaml.safe_load(atlas_path.read_text())
        reg = self._load_registry(atlas_data)
        if not reg:
            return manifests

        masker = DeterministicMasker(reg)
        config_hash = reg.config_hash()

        for html_path in filings_dir.glob("*.html"):
            try:
                text = html_path.read_text(encoding="utf-8", errors="replace")
                artifact_id = html_path.stem
                masked_text, _sanitized_meta, audit, summary = masker.mask_and_sanitize_metadata(
                    text,
                    {"source": str(html_path.name), "registry_id": reg.metadata.registry_id},
                    config_hash,
                )

                # Save anonymized
                out_path = self.anonymized_dir / "sec" / f"{artifact_id}.md"
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

        import yaml

        atlas_data = yaml.safe_load(atlas_path.read_text())
        reg = self._load_registry(atlas_data)
        if not reg:
            return manifests

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

    def _load_registry(self, atlas_data: dict[str, Any]) -> EntityRegistry | None:
        from ..identity import EntityRegistry
        from ..identity.schemas import EntityType, MatchPolicy

        try:
            reg_meta = atlas_data.get("registry", {})
            reg = EntityRegistry.create(
                company_id=reg_meta.get("company_id", self.ticker),
                registry_id=reg_meta.get("registry_id", f"reg-{self.ticker}"),
            )

            def _lookup_etype(name: str) -> EntityType:
                for et in EntityType:
                    if et.value == name:
                        return et
                return EntityType.COMPANY

            def _lookup_mpolicy(name: str) -> MatchPolicy:
                for mp in MatchPolicy:
                    if mp.value == name:
                        return mp
                return MatchPolicy.LITERAL

            for ent in atlas_data.get("entities", []):
                eid = ent.get("entity_id", "")
                etype = _lookup_etype(ent.get("entity_type", "company"))
                value = ent.get("canonical_private_value", "")
                if eid and value:
                    try:
                        reg.add_entity(eid, etype, value)
                    except ValueError:
                        pass

            for ali in atlas_data.get("aliases", []):
                aid = ali.get("alias_id", "")
                eid = ali.get("canonical_entity_id", "")
                value = ali.get("private_alias_value", "")
                etype = _lookup_etype(ali.get("entity_type", "company"))
                mpolicy = _lookup_mpolicy(ali.get("match_policy", "literal"))
                if aid and eid and value:
                    try:
                        reg.add_alias(aid, eid, value, etype, mpolicy)
                    except ValueError:
                        pass

            return reg
        except Exception as exc:
            logger.warning("Failed to load registry: %s", exc)
            return None
