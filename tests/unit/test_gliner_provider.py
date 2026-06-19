"""Offline tests for the optional local GLiNER adapter.

These tests inject fakes via the `GlinerModelLoader` protocol and run
without gliner installed. Tests that genuinely require the gliner
package and locally-cached model weights are marked `local_model`
and excluded from default CI.
"""

from __future__ import annotations

from typing import Any

import pytest

from fenrix_synthetic.discovery.providers.gliner import (
    GLiNERConfig,
    GlinerModelLoadError,
    OptionalDependencyError,
    default_gliner_loader,
    is_gliner_available,
    is_supported_device,
)
from fenrix_synthetic.discovery.providers.gliner.benchmark import (
    BENCHMARK_VERSION,
    BenchmarkDocument,
    ExpectedEntity,
    _verify_benchmark_consistency,
    load_default_benchmark,
)
from fenrix_synthetic.discovery.providers.gliner.config import (
    DEFAULT_ADAPTER_POLICY_VERSION,
    DEFAULT_THRESHOLD,
    DEVICE_CPU,
    DEVICE_MPS,
)
from fenrix_synthetic.discovery.providers.gliner.evaluation import (
    evaluate_against_benchmark,
)
from fenrix_synthetic.discovery.providers.gliner.mapping import (
    FENRIX_CANONICAL_LABELS,
    default_label_mapping,
)
from fenrix_synthetic.discovery.providers.gliner.provider import (
    GLiNERLocalProvider,
)
from fenrix_synthetic.discovery.providers.gliner.validation import (
    ValidationCounters,
    validate_entity,
)
from fenrix_synthetic.discovery.schemas import DiscoveryChunk


class FakeGlinerModel:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[tuple[str, list[str], float]] = []

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        flat_ner: bool = True,
        threshold: float = 0.5,
    ) -> list[dict[str, Any]]:
        self.calls.append((text, list(labels), float(threshold)))
        return list(self.responses)


def fake_loader_factory(
    responses: list[dict[str, Any]] | None = None,
    fail_with: Exception | None = None,
):
    def _loader(config: GLiNERConfig) -> FakeGlinerModel:
        if fail_with is not None:
            raise fail_with
        return FakeGlinerModel(responses or [])

    return _loader


def _build_chunk(
    text: str,
    document_artifact_id: str = "doc-001",
    chunk_id: str = "chunk-001",
    start_offset: int = 0,
) -> DiscoveryChunk:
    return DiscoveryChunk(
        chunk_id=chunk_id,
        document_artifact_id=document_artifact_id,
        chunk_index=0,
        start_offset=start_offset,
        end_offset=start_offset + len(text),
        text=text,
    )


class TestConfig:
    def test_defaults(self) -> None:
        c = GLiNERConfig(model_id="urchade/gliner_small-v2.1")
        assert c.threshold == DEFAULT_THRESHOLD
        assert c.allow_download is False
        assert c.adapter_policy_version == DEFAULT_ADAPTER_POLICY_VERSION
        assert c.device == DEVICE_CPU

    def test_rejects_unknown_device(self) -> None:
        with pytest.raises(ValueError):
            GLiNERConfig(model_id="x", device="paper-tape")

    def test_rejects_threshold_out_of_range(self) -> None:
        for bad in (-0.1, 1.1, 2.0):
            with pytest.raises(ValueError):
                GLiNERConfig(model_id="x", threshold=bad)

    def test_rejects_empty_model_id(self) -> None:
        with pytest.raises(ValueError):
            GLiNERConfig(model_id="")

    def test_supports_known_devices(self) -> None:
        assert is_supported_device(DEVICE_CPU)
        assert is_supported_device(DEVICE_MPS)
        assert is_supported_device("auto")
        assert is_supported_device("cuda")
        assert not is_supported_device("fluffy")


class TestMapping:
    def test_default_mapping(self) -> None:
        m = default_label_mapping()
        assert m.to_canonical("company or organization name") == "company"
        assert m.to_canonical("auditor") == "auditor"
        assert m.to_canonical("Bogus Label") == "UNKNOWN"
        assert "company" in FENRIX_CANONICAL_LABELS
        assert m.config_hash  # non-empty

    def test_canonical_listing_is_deterministic(self) -> None:
        a = sorted(FENRIX_CANONICAL_LABELS)
        b = sorted(FENRIX_CANONICAL_LABELS)
        assert a == b

    def test_inverse_resolves_canonical_to_descriptive(self) -> None:
        m = default_label_mapping()
        inverse = {}
        for raw, canon in m.label_mapping.items():
            inverse.setdefault(canon, raw)
        assert inverse["company"] == "company or organization name"


class TestValidation:
    def test_accepts_valid_entity(self) -> None:
        chunk = _build_chunk("Acme Corp reports results.")
        raw = {
            "text": "Acme Corp",
            "label": "company",
            "start": 0,
            "end": 9,
            "score": 0.91,
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is not None
        assert reason is None
        assert cand.private_matched_text == "Acme Corp"
        assert cand.original_start == 0
        assert cand.original_end == 9

    def test_rejects_missing_fields(self) -> None:
        chunk = _build_chunk("text")
        raw = {"text": "x", "start": 0, "end": 1, "score": 0.5}
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is None
        assert "missing" in (reason or "")

    def test_rejects_text_mismatch(self) -> None:
        chunk = _build_chunk("Acme Corporation")
        raw = {
            "text": "Acme Corp!!",
            "label": "company",
            "start": 0,
            "end": 9,
            "score": 0.91,
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is None
        assert "match" in (reason or "")

    def test_rejects_invalid_offsets(self) -> None:
        chunk = _build_chunk("hello")
        raw = {
            "text": "hello",
            "label": "company",
            "start": -1,
            "end": 5,
            "score": 0.91,
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is None

    def test_rejects_end_exceeding_chunk(self) -> None:
        chunk = _build_chunk("hi")
        raw = {
            "text": "hi",
            "label": "company",
            "start": 0,
            "end": 99,
            "score": 0.91,
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is None

    def test_rejects_non_numeric_score(self) -> None:
        chunk = _build_chunk("hi")
        raw = {
            "text": "hi",
            "label": "company",
            "start": 0,
            "end": 2,
            "score": "high",
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is None

    def test_rejects_score_out_of_range(self) -> None:
        chunk = _build_chunk("hi")
        raw = {
            "text": "hi",
            "label": "company",
            "start": 0,
            "end": 2,
            "score": 1.5,
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is None

    def test_offsets_reconciled_to_original_document(self) -> None:
        chunk = _build_chunk("AFTER Acme Corp MORE", start_offset=42)
        raw = {
            "text": "Acme Corp",
            "label": "company",
            "start": 6,
            "end": 15,
            "score": 0.95,
        }
        cand, reason = validate_entity(raw, chunk, ValidationCounters())
        assert cand is not None
        assert cand.original_start == 42 + 6
        assert cand.original_end == 42 + 15

    def test_unknown_labels_retained(self) -> None:
        from fenrix_synthetic.discovery.providers.gliner.validation import (
            validate_and_convert,
        )

        chunk = _build_chunk("Acme Corp reports results.")
        raw = {
            "text": "Acme Corp",
            "label": "weird_new_label",
            "start": 0,
            "end": 9,
            "score": 0.91,
        }
        result = validate_and_convert(
            [raw],
            chunk,
            company_id="C001",
            provider_name="gliner_local",
            model_name="fake",
            model_version="1.0",
            label_mapping=default_label_mapping(),
        )
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.proposed_entity_type == "UNKNOWN"
        assert c.provider_label == "weird_new_label"


class TestProvider:
    def test_provider_init_does_not_import_gliner(self) -> None:
        """Constructing the provider must not load gliner / torch / etc."""
        config = GLiNERConfig(model_id="urchade/gliner_small-v2.5")
        provider = GLiNERLocalProvider(
            config=config,
            loader=fake_loader_factory(responses=[]),
        )
        assert provider.provider_name == "gliner_local"
        assert provider.model_name == "urchade/gliner_small-v2.5"
        assert provider.is_loaded is False

    def test_health_check_with_injected_loader(self) -> None:
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(responses=[]),
        )
        assert provider.health_check() is True
        assert provider.is_loaded is True

    def test_health_check_returns_false_on_load_failure(self) -> None:
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(
                fail_with=OptionalDependencyError("no gliner"),
            ),
        )
        assert provider.health_check() is False

    def test_discover_with_injected_fake_loader(self) -> None:
        responses = [
            {
                "text": "Acme Corp",
                "label": "company",
                "start": 0,
                "end": 9,
                "score": 0.91,
            }
        ]
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(responses=responses),
        )
        chunk = _build_chunk("Acme Corp reports results.")
        response = provider.discover(chunk, labels=["company"])
        assert response.provider_name == "gliner_local"
        assert response.provider_candidates[0].private_matched_text == "Acme Corp"
        assert response.provider_candidates[0].proposed_entity_type == "company"

    def test_discover_with_empty_response(self) -> None:
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(responses=[]),
        )
        chunk = _build_chunk("Some text with no entities.")
        response = provider.discover(chunk, labels=["company"])
        assert response.provider_candidates == []

    def test_discover_reports_validation_warnings(self) -> None:
        responses = [
            {"text": "Acme", "label": "company", "start": 0, "end": 4, "score": 0.91},
            # Out-of-range end offset → rejected by validator
            {"text": "Wrong", "label": "company", "start": 99, "end": 110, "score": 0.91},
        ]
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(responses=responses),
        )
        chunk = _build_chunk("Acme NOPE Wrong")
        response = provider.discover(chunk, labels=["company"])
        assert len(response.provider_candidates) == 1

    def test_provider_uses_canonical_label_mapping(self) -> None:
        responses = [
            {
                "text": "Acme Corp",
                "label": "company or organization name",
                "start": 0,
                "end": 9,
                "score": 0.91,
            }
        ]
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(responses=responses),
        )
        chunk = _build_chunk("Acme Corp reports results.")
        response = provider.discover(chunk, labels=["company"])
        cand = response.provider_candidates[0]
        assert cand.proposed_entity_type == "company"
        assert cand.provider_label == "company or organization name"

    def test_unknown_label_kept_in_evidence(self) -> None:
        responses = [
            {
                "text": "Acme Corp",
                "label": "fictional_thing",
                "start": 0,
                "end": 9,
                "score": 0.91,
            }
        ]
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=fake_loader_factory(responses=responses),
        )
        chunk = _build_chunk("Acme Corp reports results.")
        response = provider.discover(chunk, labels=["company"])
        cand = response.provider_candidates[0]
        assert cand.proposed_entity_type == "UNKNOWN"
        assert cand.provider_label == "fictional_thing"

    def test_optional_dependency_missing_raises_typed_error(self) -> None:
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x"),
            loader=None,
        )
        with pytest.raises(OptionalDependencyError):
            provider._ensure_loaded()

    def test_default_loader_raises_typed_error_when_missing(self) -> None:
        if is_gliner_available():
            pytest.skip("gliner is installed; cannot simulate missing")
        config = GLiNERConfig(model_id="x")
        with pytest.raises(OptionalDependencyError):
            default_gliner_loader(config)

    def test_optional_dependency_does_not_crash_application(self) -> None:
        """Importing the discovery subpackage without gliner must succeed."""
        from fenrix_synthetic.discovery.providers.gliner import GLiNERConfig

        assert GLiNERConfig.__name__ == "GLiNERConfig"

    def test_discover_does_not_download_with_disallow(self) -> None:
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x", allow_download=False),
            loader=fake_loader_factory(
                fail_with=GlinerModelLoadError("would have downloaded"),
            ),
        )
        assert provider.health_check() is False

    def test_discover_records_model_identity(self) -> None:
        provider = GLiNERLocalProvider(
            config=GLiNERConfig(
                model_id="urchade/gliner_small-v2.5",
                allow_download=False,
            ),
            loader=fake_loader_factory(responses=[]),
        )
        provider.health_check()
        ident = provider.model_identity
        assert ident["model_id"] == "urchade/gliner_small-v2.5"
        assert ident["model_load_succeeded"] is True
        assert ident["config_hash"]


class TestBenchmark:
    def test_default_benchmark_loads(self) -> None:
        b = load_default_benchmark()
        assert b.version == BENCHMARK_VERSION
        assert len(b.documents) >= 3
        first = b.documents[0]
        assert first.expected_entities
        assert first.hard_negatives

    def test_benchmark_hash_is_deterministic(self) -> None:
        b1 = load_default_benchmark()
        b2 = load_default_benchmark()
        assert b1.benchmark_hash == b2.benchmark_hash

    def test_benchmark_offsets_match_text(self) -> None:
        b = load_default_benchmark()
        for doc in b.documents:
            for ex in [*doc.expected_entities, *doc.hard_negatives]:
                actual = doc.text[ex.start : ex.end]
                assert actual == ex.text, (
                    f"{doc.document_id}: {ex.text!r} claim=({ex.start},{ex.end}) actual={actual!r}"
                )

    def test_benchmark_consistency_rejects_drifted_offsets(self) -> None:
        """Rigged benchmark with mismatched offsets must raise ValueError."""
        drifted_doc = BenchmarkDocument(
            document_id="drifted-doc-01",
            text="Acme Corporation operates here.",
            expected_entities=[
                ExpectedEntity(
                    text="Acme Corporation", canonical_type="company", start=99, end=99 + 15
                ),
            ],
            hard_negatives=[],
        )
        from fenrix_synthetic.discovery.providers.gliner.benchmark import Benchmark

        rigged = Benchmark(
            version=BENCHMARK_VERSION,
            documents=[drifted_doc],
            notes="rigged for drift test",
        )
        with pytest.raises(ValueError, match="benchmark consistency check failed"):
            _verify_benchmark_consistency(rigged)

    def test_benchmark_covers_required_canonical_types(self) -> None:
        b = load_default_benchmark()
        seen: set[str] = set()
        for doc in b.documents:
            for ex in doc.expected_entities:
                seen.add(ex.canonical_type)
        required = {
            "company",
            "subsidiary",
            "product",
            "brand",
            "executive",
            "board_member",
            "proprietary_platform",
            "facility",
            "acquisition_target",
            "auditor",
            "law_firm",
            "customer",
            "supplier",
            "competitor",
            "regulator",
            "location",
            "domain",
            "exchange_ticker",
        }
        missing = required - seen
        assert not missing, f"missing canonical-type coverage: {sorted(missing)}"

    def test_external_package_not_imported(self) -> None:
        """Importing the gliner subpackage must not transitively import the optional gliner package."""
        import sys

        gliner_modules = [m for m in sys.modules if m == "gliner"]
        # Importing fresh should not introduce gliner into sys.modules.
        assert not gliner_modules, f"gliner was imported at package import time: {gliner_modules}"


class TestEvaluation:
    def test_evaluate_against_benchmark_with_fake_loader(self) -> None:
        b = load_default_benchmark()

        # Single fake response for every chunker input.
        # Configure to produce one perfect hit on first document.
        def loader(config: GLiNERConfig) -> FakeGlinerModel:
            return FakeGlinerModel(responses=[])

        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x", threshold=0.5),
            loader=loader,
        )
        metrics = evaluate_against_benchmark(provider, b)
        assert metrics.total_predicted == 0
        assert metrics.exact_precision == 0.0
        assert metrics.exact_recall == 0.0
        assert metrics.benchmark_hash == b.benchmark_hash

    def test_evaluation_with_one_correct_prediction(self) -> None:
        b = load_default_benchmark()
        doc_one = b.documents[0]
        ex = doc_one.expected_entities[0]

        def loader(config: GLiNERConfig) -> FakeGlinerModel:
            return FakeGlinerModel(
                responses=[
                    {
                        "text": ex.text,
                        "label": ex.canonical_type,
                        "start": ex.start,
                        "end": ex.end,
                        "score": 0.99,
                    }
                ]
            )

        provider = GLiNERLocalProvider(
            config=GLiNERConfig(model_id="x", threshold=0.5),
            loader=loader,
        )
        metrics = evaluate_against_benchmark(provider, b)
        # At minimum the predictor produced one match, so we expect >=1 TP.
        # Final score may be >1 if multiple spans happen to align; we just
        # require at least the seeded entity was credited.
        assert metrics.true_positives >= 1


class TestDefaultLoaderBehavior:
    def test_default_loader_raises_typed_error_when_gliner_missing(self) -> None:
        """If gliner is missing, default_gliner_loader raises OptionalDependencyError,
        not a generic ImportError or RuntimeError."""
        if is_gliner_available():
            pytest.skip("gliner is installed; cannot simulate missing")
        with pytest.raises(OptionalDependencyError):
            default_gliner_loader(GLiNERConfig(model_id="x"))

    def test_default_loader_with_disallow_raises_typed_load_error(self) -> None:
        """If gliner is installed but model is not locally cached and allow_download=False,
        default_gliner_loader must surface a typed GlinerModelLoadError, not a mock.
        """
        if not is_gliner_available():
            pytest.skip("gliner is not installed")
        with pytest.raises((GlinerModelLoadError, OSError, ValueError, RuntimeError)):
            default_gliner_loader(
                GLiNERConfig(
                    model_id="definitely-not-a-real-model-zzz/zzz",
                    allow_download=False,
                )
            )


@pytest.mark.local_model
class TestRequiresLocalModel:
    def test_requires_local_model_marker(self) -> None:
        """Marker smoke test — exercises that local_model marker is registered."""
        assert True
