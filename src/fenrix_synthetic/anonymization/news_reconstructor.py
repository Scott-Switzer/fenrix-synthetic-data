"""Synthetic news reconstructor for Phase 8B.

Reconstructs synthetic news briefs from private source events (8-K filings,
filing sections, news archive) into public synthetic event briefs.

Public output rules:
- No original headlines, exact article URLs, publication names, exact dates
- No exact deal values, named counterparties (if identifying), executive quotes
- No unique acquisition names, unique litigation names, source ticker/company names
- No raw 8-K item text

Public output should contain:
- Relative period (e.g., "Year -2, Q3")
- Event class (from controlled taxonomy)
- Synthetic event title
- Broad description
- Market/financial relevance
- Uncertainty note
- Anonymized company ID only
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

# ── Controlled event classes ──────────────────────────────────────────────

EVENT_CLASSES: tuple[str, ...] = (
    "demand_shift",
    "margin_pressure",
    "regulatory_development",
    "capital_allocation",
    "leadership_governance",
    "litigation_legal_risk",
    "supply_chain",
    "product_category_expansion",
    "macro_sensitivity",
    "financing_liquidity",
    "strategic_investment",
    "competitive_pressure",
)


@dataclass
class PrivateSourceEvent:
    """A private source event used as input for synthetic news generation.

    Contains sensitive source data that must NEVER appear in public output.
    """

    event_id: str
    event_class: str  # Must be one of EVENT_CLASSES
    source_type: str  # e.g., "8-K", "filing_section", "news_archive"
    source_date: str  # ISO date, private
    source_headline: str = ""  # Original headline, PRIVATE
    source_body: str = ""  # Original body text, PRIVATE
    source_url: str = ""  # Original URL, PRIVATE
    source_company: str = ""  # Original company name, PRIVATE
    source_ticker: str = ""  # Original ticker, PRIVATE
    deal_value: float | None = None  # Exact deal value, PRIVATE
    counterparty: str = ""  # Named counterparty, PRIVATE


@dataclass
class SyntheticNewsBrief:
    """A public synthetic news brief — safe for release."""

    brief_id: str
    company_id: str  # Anonymized company ID only
    event_class: str
    synthetic_title: str
    broad_description: str
    market_relevance: str
    relative_period: str
    uncertainty_note: str = ""
    provenance_hash: str = ""

    def to_markdown(self) -> str:
        """Render as markdown for public output."""
        return (
            f"# {self.synthetic_title}\n\n"
            f"**Event Class:** {self.event_class}\n"
            f"**Relative Period:** {self.relative_period}\n"
            f"**Anonymized Company:** {self.company_id}\n\n"
            f"## Description\n\n{self.broad_description}\n\n"
            f"## Market/Financial Relevance\n\n{self.market_relevance}\n\n"
            f"## Uncertainty Note\n\n{self.uncertainty_note}\n\n"
            f"---\n*Synthetic news brief — no real company data.*\n"
        )

    def to_csv_row(self) -> dict[str, str]:
        """Render as a CSV row."""
        return {
            "brief_id": self.brief_id,
            "company_id": self.company_id,
            "event_class": self.event_class,
            "synthetic_title": self.synthetic_title,
            "relative_period": self.relative_period,
            "market_relevance": self.market_relevance[:200],
        }


@dataclass
class NewsReconstructionResult:
    """Result of synthetic news reconstruction."""

    company_id: str
    source_events_processed: int
    briefs_generated: int
    event_counts_by_class: dict[str, int] = field(default_factory=dict)
    private_provenance_path: str = ""
    hash: str = ""


class NewsReconstructor:
    """Reconstruct synthetic news briefs from private source events.

    All private source data (company names, tickers, exact dates, URLs,
    deal values, counterparties) are sanitized before public output.
    Only the event class, broad description, and relative period appear
    in public briefs.
    """

    # Mapping of event classes to synthetic title templates
    _TITLE_TEMPLATES: dict[str, str] = {
        "demand_shift": "Shift in Customer Demand Patterns",
        "margin_pressure": "Margin Compression Event",
        "regulatory_development": "Regulatory Framework Update",
        "capital_allocation": "Capital Deployment Decision",
        "leadership_governance": "Leadership and Governance Change",
        "litigation_legal_risk": "Legal and Regulatory Risk Event",
        "supply_chain": "Supply Chain Adjustment",
        "product_category_expansion": "Product or Service Category Update",
        "macro_sensitivity": "Macroeconomic Sensitivity Event",
        "financing_liquidity": "Financing and Liquidity Activity",
        "strategic_investment": "Strategic Investment Initiative",
        "competitive_pressure": "Competitive Landscape Development",
    }

    _DESCRIPTION_TEMPLATES: dict[str, str] = {
        "demand_shift": (
            "The company experienced a shift in customer demand patterns "
            "during the relative period. Volume indicators showed directional "
            "change across key segments, affecting revenue composition."
        ),
        "margin_pressure": (
            "Margin structure was affected by changing input costs and pricing "
            "dynamics. Gross and operating margins showed movement relative to "
            "prior periods, reflecting competitive and cost factors."
        ),
        "regulatory_development": (
            "A regulatory development introduced new compliance requirements "
            "or policy changes affecting industry participants. The company "
            "adjusted its operational and reporting practices accordingly."
        ),
        "capital_allocation": (
            "A capital allocation decision was executed, involving changes to "
            "the company's investment, distribution, or financing structure. "
            "The action reflected strategic priorities and market conditions."
        ),
        "leadership_governance": (
            "A leadership or governance change occurred during the period. "
            "The transition involved adjustments to the management structure "
            "and may have implications for strategic direction."
        ),
        "litigation_legal_risk": (
            "A legal or regulatory matter came to attention during the period. "
            "The company addressed the issue through established processes, "
            "with potential financial and operational implications."
        ),
        "supply_chain": (
            "Supply chain conditions required operational adjustments. Changes "
            "in supplier relationships, logistics, or sourcing strategies "
            "affected production and delivery capabilities."
        ),
        "product_category_expansion": (
            "The company expanded or modified its product or service offerings. "
            "New category presence or enhanced capabilities aimed to address "
            "evolving market needs."
        ),
        "macro_sensitivity": (
            "Macroeconomic factors including interest rates, currency movements, "
            "or broad economic conditions affected the company's operating "
            "environment and financial performance."
        ),
        "financing_liquidity": (
            "A financing or liquidity event occurred, involving changes to the "
            "company's capital structure, credit arrangements, or cash "
            "management strategy."
        ),
        "strategic_investment": (
            "A strategic investment was made to strengthen competitive "
            "positioning. The initiative involved committing resources to "
            "growth opportunities or capability development."
        ),
        "competitive_pressure": (
            "Competitive dynamics shifted during the period, affecting market "
            "share, pricing power, or strategic positioning. The company "
            "responded through operational and strategic adjustments."
        ),
    }

    _RELEVANCE_TEMPLATES: dict[str, str] = {
        "demand_shift": (
            "Changes in demand patterns affect revenue trajectory, segment mix, "
            "and capacity utilization. Analysts should assess whether the shift "
            "is structural or cyclical."
        ),
        "margin_pressure": (
            "Margin changes directly impact profitability and cash flow generation. "
            "The persistence and drivers of margin pressure should be evaluated "
            "against industry benchmarks."
        ),
        "regulatory_development": (
            "Regulatory changes can alter compliance costs, market access, and "
            "competitive dynamics. The scope and timeline of adaptation are "
            "relevant to financial projections."
        ),
        "capital_allocation": (
            "Capital allocation decisions signal management's strategic priorities "
            "and confidence in future cash flows. Impact on capital structure and "
            "returns should be analyzed."
        ),
        "leadership_governance": (
            "Leadership changes can influence strategic direction, operational "
            "execution, and stakeholder confidence. The transition period and "
            "succession planning are key factors."
        ),
        "litigation_legal_risk": (
            "Legal and regulatory matters carry potential financial exposure and "
            "reputational impact. Disclosure adequacy and reserve provisioning "
            "should be reviewed."
        ),
        "supply_chain": (
            "Supply chain adjustments affect production costs, delivery reliability, "
            "and working capital. The strategic rationale and execution risk should "
            "be assessed."
        ),
        "product_category_expansion": (
            "Product/service expansion opens new revenue opportunities but carries "
            "execution risk and investment requirements. Market sizing and "
            "competitive response are key considerations."
        ),
        "macro_sensitivity": (
            "Macroeconomic exposure affects revenue, costs, and valuation. "
            "Sensitivity analysis across scenarios helps assess the range of "
            "potential outcomes."
        ),
        "financing_liquidity": (
            "Financing events affect capital structure, cost of capital, and "
            "financial flexibility. Liquidity adequacy and covenant compliance "
            "should be evaluated."
        ),
        "strategic_investment": (
            "Strategic investments create optionality but require capital commitment. "
            "Return on investment, payback period, and strategic fit are key "
            "evaluation criteria."
        ),
        "competitive_pressure": (
            "Competitive dynamics influence pricing, market share, and profitability. "
            "The company's competitive advantages and response strategies are "
            "relevant to long-term positioning."
        ),
    }

    def reconstruct(
        self,
        company_id: str,
        source_events: list[PrivateSourceEvent],
        ref_year: int = 2026,
    ) -> list[SyntheticNewsBrief]:
        """Reconstruct synthetic news briefs from private source events.

        Args:
            company_id: Anonymized company ID for public output.
            source_events: List of private source events.
            ref_year: Reference year for relative period calculation.

        Returns:
            List of SyntheticNewsBrief objects, safe for public release.
        """
        briefs: list[SyntheticNewsBrief] = []

        for _i, event in enumerate(source_events):
            # Validate event class
            if event.event_class not in EVENT_CLASSES:
                continue

            # Compute relative period
            relative_period = self._compute_relative_period(
                event.source_date, ref_year
            )

            # Generate synthetic content
            title = self._TITLE_TEMPLATES.get(
                event.event_class, "Corporate Event Update"
            )
            description = self._DESCRIPTION_TEMPLATES.get(
                event.event_class,
                "A corporate event occurred during the relative period.",
            )
            relevance = self._RELEVANCE_TEMPLATES.get(
                event.event_class,
                "This event has implications for financial analysis.",
            )

            # Compute provenance hash (references source privately, never exposed)
            prov_hash = hashlib.sha256(
                f"{event.source_date}:{event.event_class}:{event.source_headline}".encode()
            ).hexdigest()[:12]

            brief_id = f"news_{company_id.lower()}_{event.event_class}_{prov_hash}"

            brief = SyntheticNewsBrief(
                brief_id=brief_id,
                company_id=company_id,
                event_class=event.event_class,
                synthetic_title=title,
                broad_description=description,
                market_relevance=relevance,
                relative_period=relative_period,
                uncertainty_note=(
                    "This brief is a synthetic reconstruction from incomplete "
                    "source data. The actual event details, timing, and magnitude "
                    "may differ. Students should treat this as indicative, not factual."
                ),
                provenance_hash=prov_hash,
            )
            briefs.append(brief)

        return briefs

    def write_public_outputs(
        self,
        briefs: list[SyntheticNewsBrief],
        public_dir: Path,
    ) -> tuple[Path, Path]:
        """Write public synthetic news briefs as markdown and CSV.

        Args:
            briefs: List of synthetic news briefs.
            public_dir: Directory to write outputs to.

        Returns:
            Tuple of (markdown_path, csv_path).
        """
        public_dir.mkdir(parents=True, exist_ok=True)

        # Write combined markdown
        md_content = "# Synthetic News Briefs\n\n"
        md_content += "*These are synthetic reconstructions for classroom use. "
        md_content += "No real company data, headlines, or identifiers are included.*\n\n"

        for brief in briefs:
            md_content += brief.to_markdown()
            md_content += "\n\n"

        md_path = public_dir / "synthetic_news_briefs.md"
        md_path.write_text(md_content, encoding="utf-8")

        # Write event timeline CSV
        csv_path = public_dir / "event_timeline.csv"
        with open(csv_path, "w", newline="") as f:
            if briefs:
                writer = csv.DictWriter(f, fieldnames=list(briefs[0].to_csv_row().keys()))
                writer.writeheader()
                for brief in briefs:
                    writer.writerow(brief.to_csv_row())

        return md_path, csv_path

    def write_private_provenance(
        self,
        source_events: list[PrivateSourceEvent],
        briefs: list[SyntheticNewsBrief],
        private_dir: Path,
        company_id: str,
    ) -> Path:
        """Write private provenance map (hashes only — no original values).

        Args:
            source_events: Original private source events.
            briefs: Generated synthetic briefs.
            private_dir: Private directory for provenance output.
            company_id: Anonymized company ID.

        Returns:
            Path to the provenance file.
        """
        private_dir.mkdir(parents=True, exist_ok=True)

        provenance: dict[str, Any] = {
            "company_id": company_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "source_event_count": len(source_events),
            "brief_count": len(briefs),
            "mappings": [
                {
                    "brief_id": brief.brief_id,
                    "source_event_hash": hashlib.sha256(
                        f"{ev.source_date}:{ev.event_class}:{ev.source_headline}".encode()
                    ).hexdigest()[:12],
                    "event_class": ev.event_class,
                }
                for brief, ev in zip(briefs, source_events, strict=False)
                if brief.event_class == ev.event_class
            ],
        }

        prov_path = private_dir / "news_reconstruction_private.json"
        prov_path.write_bytes(
            orjson.dumps(provenance, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )
        return prov_path

    @staticmethod
    def _compute_relative_period(source_date: str, ref_year: int) -> str:
        """Convert an ISO source date to a relative period string.

        Args:
            source_date: ISO date string (YYYY-MM-DD).
            ref_year: Reference year.

        Returns:
            Relative period like "Year -3, Q2".
        """
        try:
            year = int(source_date[:4])
            month = int(source_date[5:7])
            quarter = (month - 1) // 3 + 1
            offset = year - ref_year
            if offset == 0:
                return f"Year 0, Q{quarter}"
            elif offset < 0:
                return f"Year {offset}, Q{quarter}"
            else:
                return f"Year +{offset}, Q{quarter}"
        except (ValueError, IndexError):
            return "[Relative Period]"
