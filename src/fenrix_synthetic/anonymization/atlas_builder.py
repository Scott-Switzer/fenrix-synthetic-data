"""Build identity atlas from collected metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson
import yaml

from ..identity import EntityRegistry
from ..identity.schemas import EntityType, MatchPolicy


class IdentityAtlasBuilder:
    """Construct a private per-company identity atlas from metadata."""

    def __init__(self, ticker: str, output_dir: Path) -> None:
        self.ticker = ticker.upper()
        self.output_dir = output_dir

    def build_from_metadata(
        self,
        yf_metadata: dict[str, Any],
        sec_results: list[Any],
        news_coverage: Any | None = None,
    ) -> EntityRegistry:
        """Build atlas from collected metadata."""
        reg = EntityRegistry.create(
            company_id=self.ticker,
            registry_id=f"atlas-{self.ticker}",
        )

        # Company name
        short_name = yf_metadata.get("short_name", "")
        long_name = yf_metadata.get("long_name", "")
        company_entity_id = f"{self.ticker}_company"
        if long_name:
            reg.add_entity(company_entity_id, EntityType.COMPANY, long_name)
            # Alias for exact canonical value
            reg.add_alias(
                f"{self.ticker}_company_alias",
                company_entity_id,
                long_name,
                EntityType.COMPANY,
                MatchPolicy.CASE_INSENSITIVE,
            )
            # Alias for short form (e.g., "NVIDIA" from "NVIDIA Corporation")
            short_form = long_name.split()[0] if " " in long_name else ""
            if short_form and short_form != long_name:
                reg.add_alias(
                    f"{self.ticker}_company_shortform",
                    company_entity_id,
                    short_form,
                    EntityType.COMPANY,
                    MatchPolicy.CASE_INSENSITIVE,
                )
        if short_name and short_name != long_name:
            reg.add_alias(
                f"{self.ticker}_company_short",
                company_entity_id,
                short_name,
                EntityType.COMPANY,
                MatchPolicy.CASE_INSENSITIVE,
            )

        # Ticker variants
        ticker_entity_id = f"{self.ticker}_ticker"
        reg.add_entity(ticker_entity_id, EntityType.TICKER, self.ticker)
        # Alias for exact ticker
        reg.add_alias(
            f"{self.ticker}_ticker_alias",
            ticker_entity_id,
            self.ticker,
            EntityType.TICKER,
            MatchPolicy.TICKER_EXACT,
        )
        # Add lowercase variant
        reg.add_alias(
            f"{self.ticker}_ticker_lower",
            ticker_entity_id,
            self.ticker.lower(),
            EntityType.TICKER,
            MatchPolicy.CASE_INSENSITIVE,
        )

        # Website / domain
        website = yf_metadata.get("website", "")
        if website:
            domain = self._extract_domain(website)
            if domain:
                domain_entity_id = f"{self.ticker}_domain"
                reg.add_entity(domain_entity_id, EntityType.COMPANY_DOMAIN, domain)
                reg.add_alias(
                    f"{self.ticker}_domain_alias",
                    domain_entity_id,
                    domain,
                    EntityType.COMPANY_DOMAIN,
                    MatchPolicy.DOMAIN_FULL,
                )
                reg.add_alias(
                    f"{self.ticker}_domain_www",
                    domain_entity_id,
                    f"www.{domain}",
                    EntityType.COMPANY_DOMAIN,
                    MatchPolicy.DOMAIN_FULL,
                )

        # City, state, country
        city = yf_metadata.get("city", "")
        state = yf_metadata.get("state", "")
        country = yf_metadata.get("country", "")
        if city:
            reg.add_entity(f"{self.ticker}_hq_city", EntityType.HEADQUARTERS, city)
        if state:
            reg.add_entity(f"{self.ticker}_hq_state", EntityType.HEADQUARTERS, state)
        if country:
            reg.add_entity(f"{self.ticker}_hq_country", EntityType.HEADQUARTERS, country)

        # Address
        address = yf_metadata.get("address1", "")
        if address:
            reg.add_entity(f"{self.ticker}_address", EntityType.FACILITY, address)

        # Phone
        phone = yf_metadata.get("phone", "")
        if phone:
            reg.add_entity(f"{self.ticker}_phone", EntityType.COMPANY, phone)

        # Exchange
        exchange = yf_metadata.get("exchange", "")
        if exchange:
            reg.add_entity(f"{self.ticker}_exchange", EntityType.COMPANY, exchange)

        # SEC CIK from results
        cik = ""
        for r in sec_results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            meta = d.get("metadata", {})
            if "cik" in meta:
                cik = meta["cik"]
                break
        if cik:
            reg.add_entity(f"{self.ticker}_cik", EntityType.CIK, cik)

        # SEC filings accession numbers
        for r in sec_results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            if d.get("artifact_type") == "filing_inventory":
                meta = d.get("metadata", {})
                filings = meta.get("filings", [])
                for i, filing in enumerate(filings[:50]):  # Limit to avoid huge atlas
                    accession = filing.get("accessionNumber", "")
                    if accession:
                        reg.add_entity(
                            f"{self.ticker}_acc_{i}",
                            EntityType.SEC_ACCESSION_NUMBER,
                            accession,
                        )

        return reg

    def save_atlas(self, atlas: EntityRegistry) -> Path:
        """Save atlas as YAML (private) and a sanitized summary."""
        atlas_path = self.output_dir / "identity_atlas.yaml"
        atlas_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "registry": {
                "registry_id": atlas.metadata.registry_id,
                "company_id": atlas.metadata.company_id,
                "schema_version": atlas.metadata.schema_version,
            },
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "entity_type": e.entity_type.value,
                    "canonical_private_value": e.canonical_private_value,
                    "assigned_pseudonym": e.assigned_pseudonym,
                }
                for e in atlas.all_entities()
            ],
            "aliases": [
                {
                    "alias_id": a.alias_id,
                    "canonical_entity_id": a.canonical_entity_id,
                    "private_alias_value": a.private_alias_value,
                    "entity_type": a.entity_type.value,
                    "match_policy": a.match_policy.value,
                }
                for a in atlas.all_aliases()
            ],
        }
        atlas_path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

        # Sanitized summary (no private values)
        summary_path = self.output_dir / "identity_atlas_summary.json"
        summary = {
            "registry_id": atlas.metadata.registry_id,
            "company_id": atlas.metadata.company_id,
            "entity_count": len(atlas.all_entities()),
            "alias_count": len(atlas.all_aliases()),
            "entity_types": sorted({e.entity_type.value for e in atlas.all_entities()}),
        }
        summary_path.write_bytes(
            orjson.dumps(summary, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        return atlas_path

    @staticmethod
    def _extract_domain(url: str) -> str | None:
        m = re.search(r"https?://(?:www\.)?([^/]+)", url)
        return m.group(1) if m else None
