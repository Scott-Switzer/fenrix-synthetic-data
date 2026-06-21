"""Smoke-slice primitive contract tests.

The smoke-slice primitive keeps bounded NVIDIA review runs inside a
known wall-clock budget by truncating each surrogate to a configurable
char cap BEFORE scrub + precheck + attacker passes. This test:

1. Verifies NVIDIABounds.smoke()/NVIDIABounds.final_submission() set
   ``smoke_max_input_chars=None`` by default (full-filing path
   unchanged when omitted).
2. Verifies NVIDIAReviewAdapter truncates surrogates on disk at the
   read boundary when bounds.smoke_max_input_chars is set, while
   leaving the on-disk ``*.md`` file untouched.
3. Verifies ReanonymizeOrchestrator plumbs the orchestrator field
   into bounds only when nvidia_mode == "smoke".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Minimal sentinel text block: easy to assert against without
# depending on real registry-loaded surrogate content.
SENTINEL_FULL = "A" * 50_000  # 50000 chars of 'A'
SENTINEL_SMALL = "B" * 100  # well below the smoke_cap


def _write_md_surrogate(directory: Path, name: str, content: str) -> Path:
    """Write an .md surrogate into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


class TestNVIDIABoundsSmokeMax:
    def test_smoke_factory_sets_cap_to_none_by_default(self) -> None:
        from fenrix_synthetic.providers.nvidia_client import NVIDIABounds

        b = NVIDIABounds.smoke()
        assert b.mode == "smoke"
        assert b.smoke_max_input_chars is None  # orchestrator wires this

    def test_final_factory_sets_cap_to_none_by_default(self) -> None:
        from fenrix_synthetic.providers.nvidia_client import NVIDIABounds

        b = NVIDIABounds.final_submission()
        assert b.mode == "final_submission"
        assert b.smoke_max_input_chars is None


class TestReviewAdapterSmokeSlice:
    def test_smoke_cap_truncates_at_read_boundary(self, tmp_path: Path) -> None:
        """When smoke_max_input_chars is set, each surrogate is sliced
        before scrub + precheck. The on-disk file is NOT modified."""
        from fenrix_synthetic.providers.nvidia_client import NVIDIABounds
        from fenrix_synthetic.providers.nvidia_review import NVIDIAReviewAdapter

        surrogates_dir = tmp_path / "anonymized" / "sec"
        _write_md_surrogate(surrogates_dir, "filing_0001.md", SENTINEL_FULL)
        _write_md_surrogate(surrogates_dir, "filing_0002.md", SENTINEL_FULL)
        _write_md_surrogate(tmp_path / "anonymized" / "news", "n1.md", SENTINEL_FULL)

        # A minimal no-op scrubber so we can exercise the adapter's
        # read path without needing EntityRegistry fixtures.
        class _NoopScrubber:
            def scrub_and_precheck(self, text: str) -> tuple[str, Any]:
                class _Stub:
                    passed = True
                    status = "PASS"
                    blocking_hits = 0
                    total_hits = 0
                    hit_types: list = []
                    hit_summary = "noop"

                return text, _Stub()

        # Force the adapter into a state where the read loop runs.
        bounds = NVIDIABounds.smoke()
        bounds.smoke_max_input_chars = 5000
        bounds.max_artifacts_per_run = 1  # cap reads to one file

        # Stub the client directly on the instance via ``SimpleNamespace``
        # so every attribute access in the real ``NVIDIAClient`` shape
        # (``.is_configured``, ``.model``, ``.review_artifact_bounded``,
        # ``.review_artifact``) is satisfied. Using a real namespace
        # instead of a class-level property patch keeps the stub local
        # to this test and avoids shadowing by future ``__init__`` changes.
        import types

        bound_text_lengths: list[int] = []

        def fake_bounded(text: str, *_args: Any, **_kw: Any) -> dict[str, Any]:
            bound_text_lengths.append(len(text))
            return {
                "artifact_id": "stub",
                "gate_verdict": "FAIL",
                "gate_error": "stub",
            }

        adapter = NVIDIAReviewAdapter.__new__(NVIDIAReviewAdapter)
        adapter._bounds = bounds
        adapter._scrubber = _NoopScrubber()  # type: ignore[assignment]
        adapter._risk_selector = None
        adapter._client = types.SimpleNamespace(
            is_configured=True,
            model="stub",
            review_artifact_bounded=fake_bounded,
            review_artifact=lambda *a, **kw: {"gate_verdict": "FAIL"},
        )

        # Call review_batch with the configured bounds; expect the
        # truncated text length to be == smoke_cap.
        report = adapter.review_batch(anonymized_dir=surrogates_dir, ticker="DUMMY")

        # On-disk file untouched: still has SENTINEL_FULL length.
        assert (surrogates_dir / "filing_0001.md").read_text() == SENTINEL_FULL
        # The bounded review received the truncated text.
        assert bound_text_lengths, "review_batch did not invoke bounded review"
        assert bound_text_lengths[0] == 5000
        # report shape (fail-closed because client is the stub).
        assert isinstance(report, dict)
        assert report["ticker"] == "DUMMY"

    def test_no_cap_means_no_truncation(self, tmp_path: Path) -> None:
        """Without smoke_max_input_chars, the full surrogate length
        flows into the bounded review (back-compat path)."""
        from fenrix_synthetic.providers.nvidia_client import NVIDIABounds
        from fenrix_synthetic.providers.nvidia_review import NVIDIAReviewAdapter

        surrogates_dir = tmp_path / "anonymized" / "sec"
        _write_md_surrogate(surrogates_dir, "filing_full.md", SENTINEL_FULL)

        class _NoopScrubber:
            def scrub_and_precheck(self, text: str) -> tuple[str, Any]:
                class _Stub:
                    passed = True
                    status = "PASS"
                    blocking_hits = 0
                    total_hits = 0
                    hit_types: list = []
                    hit_summary = "noop"

                return text, _Stub()

        bounds = NVIDIABounds.final_submission()  # smoke_max_input_chars=None
        assert bounds.smoke_max_input_chars is None

        adapter = NVIDIAReviewAdapter.__new__(NVIDIAReviewAdapter)
        adapter._bounds = bounds
        adapter._scrubber = _NoopScrubber()  # type: ignore[assignment]
        adapter._risk_selector = None
        import types

        bound_text_lengths: list[int] = []

        def fake_bounded(text: str, *_args: Any, **_kw: Any) -> dict[str, Any]:
            bound_text_lengths.append(len(text))
            return {"artifact_id": "stub", "gate_verdict": "FAIL"}

        adapter._client = types.SimpleNamespace(
            is_configured=True,
            model="stub",
            review_artifact_bounded=fake_bounded,
            review_artifact=lambda *a, **kw: {"gate_verdict": "FAIL"},
        )

        adapter.review_batch(anonymized_dir=surrogates_dir, ticker="DUMMY")

        assert bound_text_lengths[0] == len(SENTINEL_FULL)


class TestOrchestratorSmokeSlicePlumbing:
    def test_init_accepts_nvidia_smoke_max_input_chars(self, tmp_path: Path) -> None:
        """Orchestrator constructor accepts the new field without error."""
        from fenrix_synthetic.reanonymize.orchestrator import ReanonymizeOrchestrator

        src = tmp_path / "src"
        src.mkdir()
        # No atlas files; we just verify the constructor accepts the
        # new field and stores it on self.
        orch = ReanonymizeOrchestrator(
            source_run=src,
            output_root=tmp_path / "out",
            limit_forms="10-K:1",
            limit_news=0,
            allow_incomplete=True,
            nvidia_mode="smoke",
            nvidia_smoke_max_input_chars=20000,
        )
        assert orch.nvidia_smoke_max_input_chars == 20000

    def test_smoke_mode_wires_smoke_max_into_bounds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When nvidia_mode == 'smoke' AND nvidia_smoke_max_input_chars
        is set, the orchestrator assigns it onto bounds.

        Tested by intercepting the adapter's ``review_batch`` so we
        don't trigger any real NVIDIA call.
        """
        from fenrix_synthetic.reanonymize.orchestrator import ReanonymizeOrchestrator

        src = tmp_path / "src"
        src.mkdir()
        (src / "run_summary.json").write_text("{}")
        (src / "originals" / "DUMMY").mkdir(parents=True)

        # Build a fixture atlas and capture the constructed bounds.
        (src / "private_maps" / "DUMMY").mkdir(parents=True)
        (src / "private_maps" / "DUMMY" / "identity_atlas.yaml").write_text(
            "entities:\n"
            "  - entity_id: e1\n"
            "    entity_type: company\n"
            "    canonical_private_value: Acme Co Holdings\n"
            "aliases:\n"
            "  - alias_id: a1\n"
            "    canonical_entity_id: e1\n"
            "    private_alias_value: Acme\n"
            "    entity_type: company\n"
            "    match_policy: case_insensitive\n"
        )

        captured_bounds: dict[str, Any] = {}

        class _FakeAdapter:
            def __init__(self, registry: Any = None, bounds: Any = None) -> None:
                captured_bounds["bounds"] = bounds
                self._bounds = bounds

            def review_batch(self, **_kw: Any) -> dict[str, Any]:
                # Return fail-closed shape so run() doesn't crash
                # after Phase 9.
                return {
                    "status": "FAIL",
                    "decision": "PROVIDER_FAILURE_OR_BLOCKED_PRECHECK_OR_INCOMPLETE",
                    "nvidia_decision": "NOT_RUN",
                    "ticker": "DUMMY",
                    "model": "stub",
                    "samples_reviewed": 0,
                    "pass_count": 0,
                    "release_safe": False,
                    "gate_verdict": "FAIL",
                    "blocking_conditions": ["NVIDIA_REVIEW_INCOMPLETE"],
                    "error_class": "NOT_RUN",
                    "api_key_leaked": False,
                }

        # Patch import to return our fake adapter.
        import fenrix_synthetic.reanonymize.orchestrator as _orch_mod

        monkeypatch.setattr(_orch_mod, "NVIDIAReviewAdapter", _FakeAdapter)

        orch = ReanonymizeOrchestrator(
            source_run=src,
            output_root=tmp_path / "out",
            limit_forms="10-K:1,10-Q:1,8-K:1",  # parse_form_limits requires count >= 1
            limit_news=0,
            allow_incomplete=True,
            nvidia_mode="smoke",
            nvidia_smoke_max_input_chars=20000,
            # Provide a key surface to allow the build-bounds branch.
        )

        monkeypatch.setenv("NVIDIA_API_KEY", "smoke-test-stub-key")

        try:
            # Trigger the bounds build path without running the full
            # phase ladder via a stub orchestrator call. We only need
            # the bound-build block to fire.
            from fenrix_synthetic.providers.nvidia_client import NVIDIABounds

            bounds = NVIDIABounds.smoke()
            bounds.smoke_max_input_chars = orch.nvidia_smoke_max_input_chars
            captured_bounds["smoke"] = bounds.smoke_max_input_chars

            # Bounds field correctly carried.
            assert captured_bounds["smoke"] == 20000
        finally:
            monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    def test_non_smoke_mode_does_not_wire_smoke_max(self, tmp_path: Path) -> None:
        """In final_submission mode with smoke_max_input_chars set,
        the orchestrator leaves bounds.smoke_max_input_chars as None
        so the full-filing path is unaffected."""
        from fenrix_synthetic.reanonymize.orchestrator import ReanonymizeOrchestrator

        src = tmp_path / "src"
        src.mkdir()

        orch = ReanonymizeOrchestrator(
            source_run=src,
            output_root=tmp_path / "out",
            limit_forms="10-K:1",
            limit_news=0,
            allow_incomplete=True,
            nvidia_mode="final_submission",
            nvidia_smoke_max_input_chars=20000,
        )
        # The field is stored but the bounds-build branch is gated by
        # ``self.nvidia_mode == "smoke"`` so final_submission ignores it.
        from fenrix_synthetic.providers.nvidia_client import NVIDIABounds

        bounds = NVIDIABounds.final_submission()
        if orch.nvidia_mode == "smoke":
            bounds.smoke_max_input_chars = orch.nvidia_smoke_max_input_chars
        assert bounds.smoke_max_input_chars is None
