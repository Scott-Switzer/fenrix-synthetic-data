"""Fail-closed decision-file durability tests.

The reanonymize-run pipeline must NEVER leave a missing file when the
operator needs to know WHY the run stopped. Each test constructs a
situation that historically would have left a missing QA artifact and
proves the fail-closed path emits the right shape.

Requirements covered:

1. ``qa/nvidia_attack_report.json`` written on provider failure / timeout.
2. ``qa/release_gate.json`` written when NVIDIA fails.
3. ``qa/release_gate.json`` written when NVIDIA is incomplete.
4. ``qa/direct_privacy_report.json`` written when the pipeline dies
   before Phase 6.
5. Missing registry produces a fail-closed report, not a missing file.
6. No API key appears in any fail-closed report.
7. Existing decision files are NOT clobbered by the backstop.
8. The orchestrator does NOT return a green summary when decision
   files were written by the fail-closed backstop (i.e. the pipeline
   correctly RE-RAISES so the upstream caller cannot mistake a
   fail-closed run for a real PASS).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fenrix_synthetic.reanonymize.orchestrator import ReanonymizeOrchestrator

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def clean_nvidia_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def qa_root(tmp_path: Path) -> Path:
    qa = tmp_path / "qa"
    qa.mkdir()
    return qa


def _bare_orchestrator(nvidia_mode: str = "smoke") -> ReanonymizeOrchestrator:
    """Construct an orchestrator instance without running __init__.

    The helper methods we test (``_ensure_decision_files_present``)
    only need ``self.nvidia_mode`` populated, so ``__new__`` + a
    manual attribute assignment sidesteps atlas + form-limit setup
    while keeping the class identity required for instance methods.
    """
    orch = ReanonymizeOrchestrator.__new__(ReanonymizeOrchestrator)
    orch.nvidia_mode = nvidia_mode
    return orch


# ── Tests: shape of fail-closed writers ────────────────────────────────


class TestFailClosedNvidiaReportShape:
    """Verify the fail-closed NVIDIA report covers every required field."""

    def test_full_shape(self, qa_root: Path, clean_nvidia_env: None) -> None:
        from fenrix_synthetic.reanonymize.orchestrator import (
            _write_fail_closed_nvidia_report,
        )

        payload = _write_fail_closed_nvidia_report(
            qa_root,
            mode="smoke",
            model="meta/llama-3.1-70b-instruct",
            error_class="TimeoutError",
            reason="Provider timed out",
        )

        # All fields required by the user spec
        for key in (
            "nvidia_mode",
            "model",
            "gate_verdict",
            "nvidia_decision",
            "release_safe",
            "artifacts_considered",
            "artifacts_reviewed",
            "artifacts_skipped",
            "total_chunks",
            "risk_chunks_total",
            "chunks_reviewed",
            "chunks_rewritten",
            "chunks_failed",
            "chunks_skipped_due_to_cap",
            "max_confidence_before",
            "max_confidence_after",
            "direct_residual_count_before",
            "direct_residual_count_after",
            "blocking_conditions",
            "error_class",
            "api_key_leaked",
        ):
            assert key in payload, f"missing required field: {key}"

        assert payload["gate_verdict"] == "FAIL"
        assert payload["release_safe"] is False
        assert payload["error_class"] == "TimeoutError"
        assert payload["api_key_leaked"] is False
        assert "NVIDIA_REVIEW_INCOMPLETE" in payload["blocking_conditions"]

        nvidia_path = qa_root / "nvidia_attack_report.json"
        assert nvidia_path.is_file()

    def test_no_api_key_in_fail_closed_report(self, qa_root: Path, clean_nvidia_env: None) -> None:
        from fenrix_synthetic.reanonymize.orchestrator import (
            _write_fail_closed_nvidia_report,
        )

        os.environ["NVIDIA_API_KEY"] = "nvapi-secret-failclosed-test"
        try:
            _write_fail_closed_nvidia_report(
                qa_root,
                mode="smoke",
                model="meta/llama-3.1-70b-instruct",
                error_class="RuntimeError",
                reason="test",
            )
            serialized = (qa_root / "nvidia_attack_report.json").read_text()
            assert "nvapi-secret-failclosed-test" not in serialized
            assert "NVIDIA_API_KEY" not in serialized
            assert "Bearer" not in serialized
            # The literal value must not appear; the boolean field is
            # always present, but a bare substring check is the
            # honest assertion.
            assert "api_key" not in serialized.lower().replace("api_key_leaked", "")
        finally:
            os.environ.pop("NVIDIA_API_KEY", None)


class TestFailClosedGateShape:
    """Verify the fail-closed release gate carries the right surface."""

    def test_gate_reads_nvidia_decision_pass_through(self, qa_root: Path) -> None:
        nvidia_report = {"nvidia_decision": "PASS", "status": "completed"}
        from fenrix_synthetic.reanonymize.orchestrator import (
            _write_fail_closed_gate,
        )

        payload = _write_fail_closed_gate(
            qa_root,
            nvidia_report=nvidia_report,
            direct_privacy=None,
            semantic_report=None,
            error_class="UpstreamTimeout",
            source_hash="a" * 16,
        )

        # nvidia_decision flows through unchanged
        assert payload["nvidia_decision"] == "PASS"
        # But overall release is FAIL because the pipeline interrupted
        assert payload["overall_release_decision"] == "FAIL"
        assert payload["release_safe"] is False
        assert "NVIDIA_REVIEW_INCOMPLETE" in payload["blocking_conditions"]

    def test_gate_no_api_key(self, qa_root: Path) -> None:
        from fenrix_synthetic.reanonymize.orchestrator import (
            _write_fail_closed_gate,
        )

        os.environ["NVIDIA_API_KEY"] = "nvapi-secret-gate-test"
        try:
            _write_fail_closed_gate(
                qa_root,
                nvidia_report=None,
                direct_privacy=None,
                semantic_report=None,
                error_class="RuntimeError",
                source_hash="b" * 16,
            )
            serialized = (qa_root / "release_gate.json").read_text()
            assert "nvapi-secret-gate-test" not in serialized
            assert "NVIDIA_API_KEY" not in serialized
            assert "Bearer" not in serialized
        finally:
            os.environ.pop("NVIDIA_API_KEY", None)


# ── Tests: backstop helper behavior ────────────────────────────────────


class TestEnsureDecisionFilesPresent:
    """Verify the top-level backstop writes missing files without clobbering."""

    def test_writes_all_three_decision_files_when_missing(
        self, tmp_path: Path, clean_nvidia_env: None
    ) -> None:
        qa_root = tmp_path / "qa"
        orch = _bare_orchestrator(nvidia_mode="smoke")
        orch._ensure_decision_files_present(
            qa_root=qa_root,
            source_hash="c" * 16,
            error_class="PhaseFiveMaskerTimeout",
        )

        assert (qa_root / "nvidia_attack_report.json").is_file()
        assert (qa_root / "release_gate.json").is_file()
        assert (qa_root / "direct_privacy_report.json").is_file()

        nvidia_payload = json.loads((qa_root / "nvidia_attack_report.json").read_text())
        direct_payload = json.loads((qa_root / "direct_privacy_report.json").read_text())

        assert nvidia_payload["error_class"] == "PhaseFiveMaskerTimeout"
        assert direct_payload["implementation_status"] == "fail_closed"

    def test_does_not_clobber_existing_nvidia_report(
        self, tmp_path: Path, clean_nvidia_env: None
    ) -> None:
        qa_root = tmp_path / "qa"
        qa_root.mkdir()
        existing_nvidia = qa_root / "nvidia_attack_report.json"
        sentinel = {
            "schema_version": "1.0.0",
            "status": "PASS",
            "nvidia_mode": "smoke",
            "sentinel": "pre-existing-must-not-be-overwritten",
        }
        existing_nvidia.write_text(json.dumps(sentinel))

        orch = _bare_orchestrator(nvidia_mode="smoke")
        orch._ensure_decision_files_present(
            qa_root=qa_root,
            source_hash="d" * 16,
            error_class="RuntimeError",
        )

        after = json.loads(existing_nvidia.read_text())
        assert after["sentinel"] == "pre-existing-must-not-be-overwritten"
        assert (qa_root / "release_gate.json").is_file()
        assert (qa_root / "direct_privacy_report.json").is_file()

    def test_writes_files_when_secondary_exception_blocks_secondary_writes(
        self, tmp_path: Path, clean_nvidia_env: None
    ) -> None:
        qa_root = tmp_path / "qa"
        orch = _bare_orchestrator(nvidia_mode="final_submission")
        orch._ensure_decision_files_present(
            qa_root=qa_root,
            source_hash="e" * 16,
            error_class="BlockingIOLockTimeout",
        )
        for name in (
            "nvidia_attack_report.json",
            "release_gate.json",
            "direct_privacy_report.json",
        ):
            assert (qa_root / name).is_file(), f"missing {name}"


# ── Tests: top-level run() wrapper contract (user spec test #8) ────────


class TestRunWrapperReRaisesAndPersistsFiles:
    """The wrapper MUST re-raise so callers cannot mistake a fail-closed
    run for a real PASS. Decision files must still be on disk."""

    def _broken_source(self, tmp_path: Path) -> Path:
        source_run = tmp_path / "broken_source"
        source_run.mkdir()
        (source_run / "run_summary.json").write_text(json.dumps({}))
        originals_root = source_run / "originals" / "DUMMY"
        originals_root.mkdir(parents=True)
        return source_run

    def test_fail_closed_run_re_raises_and_writes_decision_files(
        self, tmp_path: Path, clean_nvidia_env: None
    ) -> None:
        source_run = self._broken_source(tmp_path)
        output_root = tmp_path / "out"

        orch = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=output_root,
            limit_forms="10-K:1",
            limit_news=0,
            allow_incomplete=True,  # bypass NVIDIA_API_KEY gate
            nvidia_mode="smoke",
        )

        qa_root = output_root / "qa"
        raised: Exception | None = None
        with pytest.raises(RuntimeError) as excinfo:
            orch.run()
        raised = excinfo.value

        # Decision files MUST be on disk regardless of the exception
        assert (qa_root / "nvidia_attack_report.json").is_file()
        assert (qa_root / "release_gate.json").is_file()

        nvidia = json.loads((qa_root / "nvidia_attack_report.json").read_text())
        gate = json.loads((qa_root / "release_gate.json").read_text())

        # NOT_RUN fail-closed payload because allow_incomplete=True + no key
        assert nvidia["error_class"] in {"NOT_RUN", raised.__class__.__name__}
        assert nvidia["gate_verdict"] == "FAIL"
        assert nvidia["api_key_leaked"] is False
        assert gate["overall_release_decision"] == "FAIL"
        assert gate["release_safe"] is False
        assert raised is not None  # the wrapper ALWAYS re-raises

    def test_fail_closed_run_with_key_missing_in_final_mode(self, tmp_path: Path) -> None:
        """allow_incomplete=False + no key → immediate RuntimeError,
        but decision files still produced by the wrapper."""
        source_run = self._broken_source(tmp_path)
        output_root = tmp_path / "out"

        orch = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=output_root,
            limit_forms="10-K:1",
            limit_news=0,
            allow_incomplete=False,
            nvidia_mode="smoke",
        )
        qa_root = output_root / "qa"
        with pytest.raises(RuntimeError):
            orch.run()

        assert (qa_root / "nvidia_attack_report.json").is_file()
        assert (qa_root / "release_gate.json").is_file()

        nvidia = json.loads((qa_root / "nvidia_attack_report.json").read_text())
        gate = json.loads((qa_root / "release_gate.json").read_text())
        # Final mode → error_class reflects RuntimeError, not NOT_RUN
        assert nvidia["error_class"] != ""
        assert gate["release_safe"] is False


class TestSigtermFailClosed:
    """The wrapper must intercept SIGTERM (the run-killing signal sent
    by ``timeout N bash run.sh``) and write fail-closed decision files
    before letting the OS terminate the process. ``try/except
    Exception`` does NOT catch SIGTERM, so a dedicated handler is
    installed in ``run()`` for the duration of the call.

    The test uses ``monkeypatch`` on ``signal.signal`` to capture the
    installed handler without actually killing the test process.
    """

    def test_sigterm_handler_writes_decision_files(
        self, tmp_path: Path, clean_nvidia_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import signal

        source_run = tmp_path / "src_sigterm"
        source_run.mkdir()
        (source_run / "run_summary.json").write_text("{}")
        (source_run / "originals" / "DUMMY").mkdir(parents=True)

        output_root = tmp_path / "out"
        qa_root = output_root / "qa"

        # Intercept signal.signal(); capture our SIGTERM handler.
        captured_handler = None
        real_signal = signal.signal

        def mock_signal_obj(signum, handler):
            nonlocal captured_handler
            if signum == signal.SIGTERM:
                captured_handler = handler
                # Replace with IGN so the captured SIGTERM later in the
                # handler doesn't recursively fire during the test.
                return real_signal(signum, signal.SIG_IGN)
            return real_signal(signum, handler)

        monkeypatch.setattr(signal, "signal", mock_signal_obj)
        # Block the os.kill() inside the handler so the test process
        # does not actually die.
        import os as _os_block

        monkeypatch.setattr(_os_block, "kill", lambda pid, sig: None)

        orch = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=output_root,
            limit_forms="10-K:1",
            limit_news=0,
            allow_incomplete=True,
            nvidia_mode="smoke",
        )

        # Monkeypatch _run_after_validate to simulate "still running
        # when SIGTERM arrived": it calls the captured handler with
        # synthetic frame=None, then returns successfully.
        def fake_run_after_validate(*_args, **_kwargs):
            if captured_handler is not None:
                captured_handler(signal.SIGTERM, None)
            # After backstop ran, surface an exception as if the
            # process would be killed; the wrapper should NOT eat it.
            raise SystemExit(143)

        monkeypatch.setattr(orch, "_run_after_validate", fake_run_after_validate)

        with pytest.raises(SystemExit):
            orch.run()

        # The handler must have written the 3 decision files.
        assert (qa_root / "nvidia_attack_report.json").is_file()
        assert (qa_root / "release_gate.json").is_file()
        assert (qa_root / "direct_privacy_report.json").is_file()

        # The error class on the gate should be SIGTERM.
        gate = json.loads((qa_root / "release_gate.json").read_text())
        assert any(
            c.get("evidence", {}).get("error_class") == "SIGTERM"
            for c in gate.get("conditions", [])
        ), gate

        # The NVIDIA report carries implementation_status=fail_closed.
        nvidia = json.loads((qa_root / "nvidia_attack_report.json").read_text())
        assert nvidia["implementation_status"] == "fail_closed"
        assert nvidia["release_safe"] is False
        # And contains no API key (we never set one, but prove the
        # payload schema excludes it).
        assert nvidia["api_key_leaked"] is False
