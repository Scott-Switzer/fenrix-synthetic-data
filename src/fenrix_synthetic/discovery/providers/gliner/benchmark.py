"""Synthetic GLiNER entity-discovery benchmark dataset.

All text and entity values in this committed benchmark are synthetic. No
real HBAN or C001 facts appear here. Every expected span documents
whether a model hit should be blocking or not, so we can measure false
positives on hard negatives.

Targets are listed for each document in the exact order they appear in
the document text. The builder walks a linear ``first-occurrence``
cursor: every target's start position must match exactly the next
occurrence of its literal text starting past the previous match. This
avoids drift between the fixture text and the recorded offsets. A
runtime consistency check inside ``load_default_benchmark`` refuses to
return a benchmark whose document text does not match its expected /
hard-negative spans.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    yaml = None  # type: ignore[assignment]


BENCHMARK_VERSION = "3.0.0"


@dataclass
class ExpectedEntity:
    text: str
    canonical_type: str
    start: int
    end: int
    blocking: bool = True
    notes: str = ""


@dataclass
class BenchmarkDocument:
    document_id: str
    text: str
    expected_entities: list[ExpectedEntity] = field(default_factory=list)
    hard_negatives: list[ExpectedEntity] = field(default_factory=list)


@dataclass
class Benchmark:
    version: str = BENCHMARK_VERSION
    documents: list[BenchmarkDocument] = field(default_factory=list)
    notes: str = (
        "All values are synthetic. No real HBAN facts appear here. "
        "hard_negatives must NOT be discovered as entities. "
        "Offsets are derived from fixture text at load time."
    )

    @property
    def benchmark_hash(self) -> str:
        payload = json.dumps(
            [
                {
                    "document_id": d.document_id,
                    "text": d.text,
                    "expected": [
                        {
                            "text": e.text,
                            "canonical_type": e.canonical_type,
                            "start": e.start,
                            "end": e.end,
                            "blocking": e.blocking,
                        }
                        for e in d.expected_entities
                    ],
                    "hard_negatives": [
                        {
                            "text": n.text,
                            "canonical_type": n.canonical_type,
                            "start": n.start,
                            "end": n.end,
                        }
                        for n in d.hard_negatives
                    ],
                }
                for d in self.documents
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "benchmark_hash": self.benchmark_hash,
            "notes": self.notes,
            "documents": [
                {
                    "document_id": d.document_id,
                    "text": d.text,
                    "expected_entities": [
                        {
                            "text": e.text,
                            "canonical_type": e.canonical_type,
                            "start": e.start,
                            "end": e.end,
                            "blocking": e.blocking,
                            "notes": e.notes,
                        }
                        for e in d.expected_entities
                    ],
                    "hard_negatives": [
                        {
                            "text": n.text,
                            "canonical_type": n.canonical_type,
                            "start": n.start,
                            "end": n.end,
                        }
                        for n in d.hard_negatives
                    ],
                }
                for d in self.documents
            ],
        }


# Each target is ``(text, canonical_type, is_expected)``.
# Targets inside a single document must be listed in the same chronological
# order they appear in the document text so the linear cursor can find
# each one. Builder raises RuntimeError on drift (text changed without
# updating the target list).
#
# Canonical-type coverage (Part 14): company, subsidiary, product, brand,
# executive, board_member, proprietary_platform, facility,
# acquisition_target, auditor, law_firm, customer, supplier, competitor,
# regulator, location, domain, exchange_ticker.
_DOC_TEMPLATES: list[tuple[str, str, list[tuple[str, str, bool]]]] = [
    (
        "bench-01-variant-company",
        (
            "Halloran Banking Group, Inc. operates as the parent of "
            "Halloran Mortgage Services LLC. The Company retains "
            "Pell & Whitlock CPA as auditor and Wallander Jangrin & Fisk LLP "
            "as legal counsel. Headquarters are in Centerville, Ohio. "
            "Recent materials discuss internal counsel notes, ongoing "
            "operations, and informal board observations shared in board "
            "prep materials for the audit committee."
        ),
        [
            ("Halloran Banking Group, Inc.", "company", True),
            ("Halloran Mortgage Services LLC", "subsidiary", True),
            ("Pell & Whitlock CPA", "auditor", True),
            ("Wallander Jangrin & Fisk LLP", "law_firm", True),
            ("Centerville, Ohio", "location", True),
            # Hard negatives — phrasings that LOOK entity-like but are not tagged
            ("internal counsel notes", "location", False),
            ("ongoing operations", "product", False),
            ("informal board observations", "facility", False),
            ("board prep materials", "customer", False),
        ],
    ),
    (
        "bench-02-product-brand-platform",
        (
            "Halloran launched HalloPay as a proprietary mobile banking "
            "platform released under the HalloranBlue brand. Operations "
            "occupy offices in Centerville West and Halloran Annex. The "
            "product attracts small business customers nationwide. "
            "Public visitors read about the launch at halloran.example.com "
            "in additional investor enclosure schedules."
        ),
        [
            ("HalloPay", "proprietary_platform", True),
            ("HalloranBlue", "brand", True),
            ("Centerville West", "facility", True),
            ("Halloran Annex", "facility", True),
            ("small business customers", "customer", True),
            # Hard negatives — additional entity-like phrases (must come
            # before halloran.example.com in the targets list to match the
            # text order)
            ("Public visitors", "facility", False),
            ("halloran.example.com", "domain", True),
            ("investor enclosure schedules", "product", False),
        ],
    ),
    (
        "bench-03-acquisition-jv-regulator",
        (
            "Halloran announced the acquisition of Pinnacle Bay Securities "
            "Holdings as part of its joint venture with Savings Coalition "
            "Partners Co. The combined entity will operate under the "
            "Halloran AlliedInvest umbrella. The Chicago office will lead "
            "the integration. Separately, competitive commentary from "
            "Bayport Banking Corp prompted a public response from "
            "Federal Reserve Board. Operations source office supplies "
            "from Murphy Office Suppliers Co. Recent internal discussions "
            "describe informal arrangements and ongoing board prep notes "
            "from internal review sessions."
        ),
        [
            ("Pinnacle Bay Securities Holdings", "acquisition_target", True),
            ("Savings Coalition Partners Co.", "joint_venture", True),
            ("Halloran AlliedInvest", "subsidiary", True),
            ("Chicago", "location", True),
            ("Bayport Banking Corp", "competitor", True),
            ("Federal Reserve Board", "regulator", True),
            ("Murphy Office Suppliers Co.", "supplier", True),
            # Hard negatives
            ("informal arrangements", "location", False),
            ("ongoing board prep notes", "facility", False),
            ("internal review sessions", "location", False),
        ],
    ),
    (
        "bench-04-ticker-executive-product",
        (
            "Halloran common stock trades on NASDAQ under the symbol HLG. "
            "The Company produces a Treasury Income Note product overseen "
            "by CEO Marisol Pelham and CFO Tomas Yairi. Board members "
            "include Helena Kwong (chair) and Marlin Quill. Recent "
            "materials discuss ongoing enrollment in the Adjusted Yield "
            "Service offering from affiliated distribution channels."
        ),
        [
            ("NASDAQ", "exchange_ticker", True),
            ("HLG", "exchange_ticker", True),
            ("Treasury Income Note", "product", True),
            ("Marisol Pelham", "executive", True),
            ("Tomas Yairi", "executive", True),
            ("Helena Kwong", "board_member", True),
            ("Marlin Quill", "board_member", True),
            # Hard negatives — entity-looking generic phrases
            ("ongoing enrollment", "customer", False),
            ("Adjusted Yield Service", "proprietary_platform", False),
            ("affiliated distribution channels", "subsidiary", False),
        ],
    ),
    (
        "bench-05-section-titles",
        (
            "Risk Factors discussion continues on the next page. Important "
            "Notice to Shareholders follows. Item 1A. Item 7. See Note 1 "
            "to Consolidated Financial Statements. The Company describes "
            "accounting policies in Note 2. Exhibit Index lists attached "
            "exhibits. Cautionary statements regarding forward-looking "
            "remarks apply throughout."
        ),
        [
            # No expected entities — every target is a hard-negative
            # (section-heading-style strings that must NOT be promoted
            # to entities even though they capitalized phrases).
            ("Risk Factors", "regulator", False),
            ("Important Notice to Shareholders", "location", False),
            ("Item 1A", "facility", False),
            ("Item 7", "facility", False),
            ("Note 1", "auditor", False),
            ("Consolidated Financial Statements", "company", False),
            ("Note 2", "auditor", False),
            ("Exhibit Index", "facility", False),
        ],
    ),
]


def _build_consistent_benchmark() -> Benchmark:
    docs: list[BenchmarkDocument] = []
    for doc_id, text, targets in _DOC_TEMPLATES:
        cursor = 0
        expected: list[ExpectedEntity] = []
        hard_negs: list[ExpectedEntity] = []
        for tgt_text, tgt_type, is_expected in targets:
            idx = text.find(tgt_text, cursor)
            if idx < 0:
                raise RuntimeError(
                    f"benchmark fixture drift: cannot locate {tgt_text!r} "
                    f"after index {cursor} in document {doc_id!r}"
                )
            end_idx = idx + len(tgt_text)
            cursor = end_idx
            if is_expected:
                expected.append(
                    ExpectedEntity(
                        text=tgt_text,
                        canonical_type=tgt_type,
                        start=idx,
                        end=end_idx,
                        blocking=True,
                        notes="",
                    )
                )
            else:
                hard_negs.append(
                    ExpectedEntity(
                        text=tgt_text,
                        canonical_type=tgt_type,
                        start=idx,
                        end=end_idx,
                        blocking=False,
                        notes="hard_negative",
                    )
                )
        docs.append(
            BenchmarkDocument(
                document_id=doc_id,
                text=text,
                expected_entities=expected,
                hard_negatives=hard_negs,
            )
        )
    return Benchmark(version=BENCHMARK_VERSION, documents=docs)


def _verify_benchmark_consistency(benchmark: Benchmark) -> None:
    mismatches: list[str] = []
    for doc in benchmark.documents:
        for ex in [*doc.expected_entities, *doc.hard_negatives]:
            actual = doc.text[ex.start : ex.end]
            if actual != ex.text:
                mismatches.append(
                    f"{doc.document_id}: {ex.text!r} claim=({ex.start},{ex.end}) actual={actual!r}"
                )
    if mismatches:
        raise ValueError(
            f"benchmark consistency check failed ({len(mismatches)} mismatches): "
            + "; ".join(mismatches)
        )


def load_default_benchmark() -> Benchmark:
    """Return the canonical synthetic benchmark; raise if offsets drifted."""
    benchmark = _build_consistent_benchmark()
    _verify_benchmark_consistency(benchmark)
    return benchmark


def load_benchmark_from_yaml(path: Path | str) -> Benchmark:
    if yaml is None:
        return load_default_benchmark()
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return load_default_benchmark()
    if not isinstance(data, dict):
        return load_default_benchmark()
    docs = data.get("documents", [])
    if not isinstance(docs, list):
        return load_default_benchmark()
    benchmark_docs: list[BenchmarkDocument] = []
    for raw in docs:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", ""))
        expected_raw = raw.get("expected_entities", []) or []
        negatives_raw = raw.get("hard_negatives", []) or []
        expected = [
            ExpectedEntity(
                text=str(e.get("text", "")),
                canonical_type=str(e.get("canonical_type", "UNKNOWN")),
                start=int(e.get("start", 0)),
                end=int(e.get("end", 0)),
                blocking=bool(e.get("blocking", True)),
                notes=str(e.get("notes", "")),
            )
            for e in expected_raw
            if isinstance(e, dict)
        ]
        negatives = [
            ExpectedEntity(
                text=str(n.get("text", "")),
                canonical_type=str(n.get("canonical_type", "UNKNOWN")),
                start=int(n.get("start", 0)),
                end=int(n.get("end", 0)),
                blocking=False,
                notes="hard_negative",
            )
            for n in negatives_raw
            if isinstance(n, dict)
        ]
        benchmark_docs.append(
            BenchmarkDocument(
                document_id=str(raw.get("document_id", "")),
                text=text,
                expected_entities=expected,
                hard_negatives=negatives,
            )
        )
    benchmark = Benchmark(
        version=str(data.get("version", BENCHMARK_VERSION)),
        documents=benchmark_docs,
        notes=str(data.get("notes", "")),
    )
    _verify_benchmark_consistency(benchmark)
    return benchmark
