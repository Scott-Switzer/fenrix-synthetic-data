"""Label mapping between GLiNER descriptive labels and Fenrix canonical types.

GLiNER is a zero-shot model that accepts free-form text labels. We accept a
set of descriptive labels produced by GLiNER (e.g. "company or organization
name") and map them to canonical Fenrix types (e.g. "company"). Unknown
GLiNER labels remain visible in the candidate evidence — they are never
silently dropped.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - pyyaml is required by the project
    yaml = None  # type: ignore[assignment]


# Canonical Fenrix entity types used downstream by the masking pipeline and
# deduplication. Exact strings — used as proposed_entity_type values.
FENRIX_CANONICAL_LABELS: tuple[str, ...] = (
    "company",
    "former_company_name",
    "executive",
    "board_member",
    "subsidiary",
    "business_segment",
    "product",
    "brand",
    "proprietary_platform",
    "facility",
    "headquarters",
    "acquisition_target",
    "joint_venture",
    "auditor",
    "law_firm",
    "customer",
    "supplier",
    "competitor",
    "regulator",
    "location",
    "domain",
    "exchange_ticker",
    "UNKNOWN",
)

VERSION = "1.0.0"


@dataclass
class EntityLabelMapping:
    """Mapping table from descriptive labels to canonical Fenrix types."""

    label_mapping: dict[str, str] = field(default_factory=dict)
    raw_labels: tuple[str, ...] = ()
    version: str = VERSION

    @property
    def config_hash(self) -> str:
        content = "\n".join(sorted(self.label_mapping.keys())) + "\n"
        content += "|".join(sorted(set(self.label_mapping.values())))
        content += f"|version={self.version}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def to_canonical(self, raw_label: str) -> str:
        """Return canonical Fenrix label, or 'UNKNOWN' if unmapped."""
        return self.label_mapping.get(raw_label, "UNKNOWN")

    def canonical_for_gliner_labels(self, gliner_labels: list[str]) -> list[str]:
        """Compute the list of canonical labels that the requested GLiNER
        labels map to. Returns deduplicated list. Unknown labels are kept
        as 'UNKNOWN'."""
        seen: set[str] = set()
        out: list[str] = []
        for gl in gliner_labels:
            c = self.to_canonical(gl)
            seen.add(c)
            out.append(c)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "config_hash": self.config_hash,
            "label_mapping": dict(self.label_mapping),
            "raw_labels": list(self.raw_labels),
            "fenrix_canonical_labels": list(FENRIX_CANONICAL_LABELS),
        }


def default_label_mapping() -> EntityLabelMapping:
    """Built-in default mapping covering the canonical Fenrix labels.

    Mirrors `configs/entity_labels.yaml` so that the adapter is fully usable
    without a labels file. Explicit user-provided mappings always override
    this default.
    """
    mapping: dict[str, str] = {
        # company
        "company or organization name": "company",
        "company": "company",
        "organization name": "company",
        "corporation": "company",
        "former company name": "former_company_name",
        # executive / board member
        "executive or board member": "executive",
        "executive": "executive",
        "ceo": "executive",
        "cfo": "executive",
        "chief executive officer": "executive",
        "chief financial officer": "executive",
        "board member": "board_member",
        # subsidiary
        "company subsidiary": "subsidiary",
        "subsidiary": "subsidiary",
        # business segment
        "business segment": "business_segment",
        "division": "business_segment",
        # product
        "commercial product or brand": "product",
        "product": "product",
        "product name": "product",
        # brand
        "brand": "brand",
        "brand name": "brand",
        # proprietary platform
        "proprietary technology platform": "proprietary_platform",
        "proprietary technology": "proprietary_platform",
        # facility / headquarters
        "corporate facility or headquarters": "facility",
        "facility": "facility",
        "headquarters": "headquarters",
        "office": "facility",
        # acquisition / JV
        "acquisition target": "acquisition_target",
        "joint venture": "joint_venture",
        # service providers
        "auditor": "auditor",
        "accounting firm": "auditor",
        "law firm": "law_firm",
        "legal counsel": "law_firm",
        # counterparties
        "customer": "customer",
        "client": "customer",
        "supplier": "supplier",
        "vendor": "supplier",
        "competitor": "competitor",
        "regulator": "regulator",
        "regulatory body": "regulator",
        # location / domain / ticker
        "location": "location",
        "city": "location",
        "country": "location",
        "headquarters location": "location",
        "domain name": "domain",
        "domain": "domain",
        "exchange ticker": "exchange_ticker",
        "ticker symbol": "exchange_ticker",
        "stock ticker": "exchange_ticker",
    }
    return EntityLabelMapping(label_mapping=mapping, raw_labels=tuple(mapping.keys()))


def load_label_mapping(path: Path | str) -> EntityLabelMapping:
    """Load a labels configuration from YAML. Falls back to default mapping
    on any parse error."""
    p = Path(path)
    if yaml is None:
        return default_label_mapping()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_label_mapping()
    if not isinstance(data, dict):
        return default_label_mapping()
    raw_mapping = data.get("label_mapping", {})
    if not isinstance(raw_mapping, dict):
        return default_label_mapping()
    cleaned: dict[str, str] = {}
    for k, v in raw_mapping.items():
        if isinstance(k, str) and isinstance(v, str):
            cleaned[k] = v
    version = str(data.get("version", VERSION)) if isinstance(data.get("version"), str) else VERSION
    return EntityLabelMapping(
        label_mapping=cleaned, raw_labels=tuple(cleaned.keys()), version=version
    )
