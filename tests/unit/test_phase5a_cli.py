"""Phase 5A CLI tests using Click's CliRunner (isolated, no subprocess).

Covers the 5 new Phase 5A commands:

* `s3-transform`
* `s3-attack`
* `s3-assess`
* `evaluate-submission`
* `atlas-harvest`

And the global privacy/release contract:

* Every command's valid path
* Missing inputs (exit 2)
* Invalid variant (exit 2)
* Nested canonical metric rejection (defect 1B)
* Ineligible release request (exit 5)
* Missing attack evidence (exit 2)
* Invalid submission action (exit 2)
* Duplicate relative periods (exit 2)
* Private output written outside the private root (exit 3)
* Sanitized output containing forbidden fields
* Direct export attempt bypassing `s3 assess` (sanitized paths only)
* Exit-code correctness
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def private_root(tmp_path: Path) -> Path:
    """Provide a writable private root outside the repo."""
    pr = tmp_path / "private"
    pr.mkdir(parents=True, exist_ok=True)
    return pr


@pytest.fixture
def prices_json(tmp_path: Path) -> Path:
    """Write a small valid OHLCV price JSON file."""
    path = tmp_path / "prices.json"
    records = []
    base = 100.0
    for i in range(260):
        base = base * 1.001 if i % 5 == 0 else base * 0.999
        records.append(
            {
                "date": f"day-{i:04d}",
                "open": base - 0.5,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base,
                "volume": 10000 + i,
            }
        )
    path.write_text(json.dumps({"records": records}))
    return path


@pytest.fixture
def candidate_universe_json(tmp_path: Path) -> Path:
    """Write a candidate universe JSON used by `s3-attack`."""
    path = tmp_path / "universe.json"
    candidates = []
    base = 100.0
    for j in range(20):
        series = []
        for i in range(252):
            base_i = base + j * 0.01
            base_i = base_i * 1.002 if i % 7 == 0 else base_i * 0.998
            series.append(round(base_i, 4))
        candidates.append({"candidate_id": f"cand-{j:03d}", "prices": series})
    path.write_text(json.dumps({"candidates": candidates}))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_group() -> Any:
    from fenrix_synthetic.cli import cli

    return cli


# ── s3-transform ─────────────────────────────────────────────────────


class TestS3Transform:
    def test_s3b_success(
        self, runner: CliRunner, cli_group, private_root: Path, prices_json: Path
    ) -> None:
        result = runner.invoke(
            cli_group,
            [
                "s3-transform",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--prices",
                str(prices_json),
            ],
            catch_exceptions=True,
        )
        assert result.exit_code == 0, (
            f"exit={result.exit_code}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "s3-transform OK" in result.stdout
        assert "variant=s3b_weekly_features" in result.stdout
        assert "marker=release_candidate" in result.stdout
        assert "relative_week" not in result.stdout
        assert "weekly_direction_category" not in result.stdout

    def test_s3c_success(
        self, runner: CliRunner, cli_group, private_root: Path, prices_json: Path
    ) -> None:
        result = runner.invoke(
            cli_group,
            [
                "s3-transform",
                "--private-root",
                str(private_root),
                "--variant",
                "s3c_block_features",
                "--prices",
                str(prices_json),
            ],
        )
        assert result.exit_code == 0
        assert "marker=release_candidate" in result.stdout

    def test_invalid_variant_exit_2(
        self, runner: CliRunner, cli_group, private_root: Path, prices_json: Path
    ) -> None:
        result = runner.invoke(
            cli_group,
            [
                "s3-transform",
                "--private-root",
                str(private_root),
                "--variant",
                "s1_basic",
                "--prices",
                str(prices_json),
            ],
        )
        assert result.exit_code == 2

    def test_missing_prices_exit_4_or_2(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        result = runner.invoke(
            cli_group,
            [
                "s3-transform",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--prices",
                "/nonexistent/path.json",
            ],
        )
        assert result.exit_code in (2, 4)

    def test_output_outside_private_root_exit_2_or_3(
        self, runner: CliRunner, cli_group, prices_json: Path, tmp_path: Path
    ) -> None:
        # No --private-root + missing env var => outputs go into cwd which
        # is inside the repo; CLI rejects with exit 2 or 3.
        repo_path = tmp_path / "in_repo_output.json"
        result = runner.invoke(
            cli_group,
            [
                "s3-transform",
                "--variant",
                "s3b_weekly_features",
                "--prices",
                str(prices_json),
                "--output",
                str(repo_path),
            ],
        )
        assert result.exit_code in (2, 3)


# ── s3-attack ────────────────────────────────────────────────────────


class TestS3Attack:
    def _run_s3_transform_s3b(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        prices_json: Path,
    ) -> Path:
        target = private_root / "s3b_features_for_attack.json"
        result = runner.invoke(
            cli_group,
            [
                "s3-transform",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--prices",
                str(prices_json),
                "--output",
                str(target),
            ],
        )
        assert result.exit_code == 0, result.stderr
        return target

    def test_s3b_attack_success(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        prices_json: Path,
        candidate_universe_json: Path,
    ) -> None:
        source = self._run_s3_transform_s3b(runner, cli_group, private_root, prices_json)
        result = runner.invoke(
            cli_group,
            [
                "s3-attack",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--source-features",
                str(source),
                "--candidate-universe",
                str(candidate_universe_json),
                "--required-attacks",
                "exact",
                "--required-attacks",
                "combined",
                "--required-ablations",
                "direction",
            ],
        )
        assert result.exit_code == 0, f"{result.exit_code}: {result.stderr}"
        assert "s3-attack OK" in result.stdout
        assert "n_missing=0" in result.stdout

    def test_missing_required_attack_exit_2(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        prices_json: Path,
        candidate_universe_json: Path,
    ) -> None:
        source = self._run_s3_transform_s3b(runner, cli_group, private_root, prices_json)
        result = runner.invoke(
            cli_group,
            [
                "s3-attack",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--source-features",
                str(source),
                "--candidate-universe",
                str(candidate_universe_json),
                "--required-attacks",
                "totally_made_up_attack_name",
            ],
        )
        assert result.exit_code == 2
        assert "required attacks/ablations missing" in result.stderr.lower()

    def test_missing_required_ablation_exit_2(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        prices_json: Path,
        candidate_universe_json: Path,
    ) -> None:
        source = self._run_s3_transform_s3b(runner, cli_group, private_root, prices_json)
        result = runner.invoke(
            cli_group,
            [
                "s3-attack",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--source-features",
                str(source),
                "--candidate-universe",
                str(candidate_universe_json),
                "--required-ablations",
                "ghost_ablation_group",
            ],
        )
        assert result.exit_code == 2

    def test_s3b_attack_no_required_filter_succeeds(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        prices_json: Path,
        candidate_universe_json: Path,
    ) -> None:
        # S3B is releasable; without eligibility issues, the CLI passes.
        source = self._run_s3_transform_s3b(runner, cli_group, private_root, prices_json)
        result = runner.invoke(
            cli_group,
            [
                "s3-attack",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--source-features",
                str(source),
                "--candidate-universe",
                str(candidate_universe_json),
            ],
        )
        assert result.exit_code == 0


# ── s3-assess ────────────────────────────────────────────────────────


def _build_canonical_attack_dict(
    variant: str = "s3b_weekly_features",
    attack_name: str = "combined",
    ablation: str = "all",
    rank: int = 200,
    universe: int = 200,
    percentile: float = 0.0,
    score: float = 0.1,
) -> dict[str, Any]:
    """Build a canonical CategoricalAttackEvidence dict suitable for `s3-assess`."""
    return {
        "variant": variant,
        "attack_name": attack_name,
        "ablation": ablation,
        "true_source_rank": rank,
        "candidate_universe_size": universe,
        "percentile_rank": percentile,
        "top_1": rank == 1,
        "top_5": 0 < rank <= 5,
        "top_10": 0 < rank <= 10,
        "score": score,
        "status": "completed",
        "attack_hash": "abchash000000000",
        "notes": "",
    }


# Required attack and ablation coverage that satisfies the gate's
# evidence-completeness precheck. The CLI surfaces are expected to
# produce all 6 * 8 = 48 combinations.
_COMPLETE_ATTACKS: tuple[str, ...] = (
    "exact",
    "weighted_hamming",
    "dtw",
    "transition",
    "ngram",
    "combined",
)
_COMPLETE_ABLATIONS: tuple[str, ...] = (
    "all",
    "direction",
    "momentum",
    "volatility",
    "drawdown",
    "market_relative",
    "sector_relative",
    "technical_state",
)


def _build_complete_evidence(
    variant: str = "s3b_weekly_features",
    rank: int = 200,
    universe: int = 200,
    percentile: float = 0.0,
    score: float = 0.1,
) -> list[dict[str, Any]]:
    """Build a *complete* evidence set (48 rows) so the gate's evidence
    completeness precheck passes and the threshold path is exercised."""
    return [
        _build_canonical_attack_dict(
            variant=variant,
            attack_name=atk,
            ablation=abl,
            rank=rank,
            universe=universe,
            percentile=percentile,
            score=score,
        )
        for atk in _COMPLETE_ATTACKS
        for abl in _COMPLETE_ABLATIONS
    ]


def _build_complete_evidence_json(
    variant: str = "s3b_weekly_features",
    **kwargs: Any,
) -> str:
    return json.dumps({"attacks": _build_complete_evidence(variant=variant, **kwargs)})


class TestS3Assess:
    def test_pass_candidate_exit_0(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        attacks_path = tmp_path / "attacks_clean.json"
        # All 48 attacks rank very low (200 outside top 10 of 200).
        attacks_path.write_text(_build_complete_evidence_json(rank=200, universe=200))
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
            ],
        )
        assert result.exit_code == 0, f"{result.exit_code}: {result.stderr}"
        assert "decision=" in result.stdout

    def test_fail_ranking_in_top_10_exit_3(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        attacks_path = tmp_path / "attacks_bad.json"
        attacks_path.write_text(
            _build_complete_evidence_json(rank=3, universe=200, percentile=98.5)
        )
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
            ],
        )
        assert result.exit_code == 3
        combined = (result.stdout + result.stderr).lower()
        assert "fail" in combined

    def test_s3a_variant_refused_exit_5(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        # S3A is rejected at the eligibility check BEFORE the threshold
        # loop, so the underlying attack content does not matter.
        attacks_path = tmp_path / "attacks_clean.json"
        attacks_path.write_text(
            _build_complete_evidence_json(variant="s3a_daily_bucketed", rank=200)
        )
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3a_daily_bucketed",
                "--attack-results",
                str(attacks_path),
            ],
        )
        assert result.exit_code == 5
        combined = result.stderr.lower()
        assert "ineligible" in combined or "not eligible" in combined

    def test_empty_attack_results_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        # Per Phase 5A close-out spec: empty/incomplete evidence → exit 2.
        attacks_path = tmp_path / "attacks_empty.json"
        attacks_path.write_text(json.dumps({"attacks": []}))
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
            ],
        )
        assert result.exit_code == 2

    def test_nested_metrics_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        # Defect 1B: a row with canonical fields nested inside 'metrics'
        # must not silently pass. The nested row shares attack_name="combined",
        # ablation="all" with one of the 48 valid rows, creating a duplicate
        # that the hardened gate's completeness precheck catches → exit 2.
        nested_dict = {
            "attack_name": "combined",
            "ablation": "all",
            "variant": "s3b_weekly_features",
            "metrics": {
                "candidate_universe_size": 200,
                "true_source_rank": 200,
                "percentile_rank": 0.0,
            },
            "top_1": False,
            "top_5": False,
            "top_10": False,
            "score": 0.1,
        }
        complete = _build_complete_evidence(rank=200, universe=200)
        attacks_path = tmp_path / "attacks_nested.json"
        attacks_path.write_text(json.dumps({"attacks": [nested_dict] + complete}))
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
            ],
        )
        # Nested row + 48 valid rows = 49 rows with one duplicate pair.
        # The gate's completeness precheck catches the duplicate → exit 2.
        assert result.exit_code == 2
        assert "[INCOMPLETE_EVIDENCE]" in (result.stdout + result.stderr)

    def test_sanitized_output_forbidden_fields(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """The `--output` JSON contains only gate-level fields, never raw
        per-attack rank details or private text."""
        attacks_path = tmp_path / "attacks.json"
        attacks_path.write_text(_build_complete_evidence_json(rank=200))
        out = private_root / "assess_sanitized.json"
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, f"{result.exit_code}: {result.stderr}"
        payload = json.loads(out.read_text())
        forbidden = [
            "private_matched_text",
            "matched_text",
            "private_hash",
            "raw_response",
            "context_excerpt",
            "matched_text_hash",
            "source_alias",
            "alias",
        ]
        serialized = json.dumps(payload)
        for token in forbidden:
            assert token not in serialized, f"{token} leaked into sanitized output"
        for required_key in ("decision", "gate_hash", "policy_hash", "evidence_count"):
            assert required_key in payload


# ── evaluate-submission ─────────────────────────────────────────────


class TestEvaluateSubmission:
    def _truth_file(self, tmp_path: Path, n_periods: int = 252) -> Path:
        path = tmp_path / "private_truth.json"
        period_returns = [0.001 * ((i % 7) - 3) for i in range(n_periods)]
        path.write_text(json.dumps({"period_returns": period_returns}))
        return path

    def test_success(
        self, runner: CliRunner, cli_group, private_root: Path, tmp_path: Path
    ) -> None:
        truth = self._truth_file(tmp_path)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "SYNTH_TEST",
                "--run-id",
                "run-test",
                "--submission-id",
                "sub-001",
                "--relative-periods",
                ",".join(str(i) for i in range(252)),
                "--binary-actions",
                ",".join("1" if i % 3 == 0 else "0" for i in range(252)),
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 0, f"{result.exit_code}: {result.stderr}"
        assert "evaluate-submission OK" in result.stdout
        assert "period_returns" not in result.stdout
        assert "0.001" not in result.stdout

    def test_duplicate_relative_periods_exit_2(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = self._truth_file(tmp_path, 10)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,1,2,3",
                "--binary-actions",
                "0,1,0,0,1",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        combined = result.stderr.lower()
        assert "duplicate" in combined or "unique" in combined

    def test_invalid_binary_action_exit_2(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = self._truth_file(tmp_path, 5)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2,3,4",
                "--binary-actions",
                "0,1,2,0,1",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        combined = result.stderr.lower()
        assert "binary" in combined or "0 or 1" in combined

    def test_period_action_length_mismatch_exit_2(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = self._truth_file(tmp_path, 10)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2,3,4",
                "--binary-actions",
                "0,1",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        combined = result.stderr.lower()
        assert "period" in combined and "action" in combined

    def test_non_monotonic_periods_exit_2(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = self._truth_file(tmp_path, 10)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,3,2,4,5",
                "--binary-actions",
                "0,1,0,0,1",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        combined = result.stderr.lower()
        assert "monotonic" in combined or "increasing" in combined

    def test_sanitized_output_omits_per_period(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = self._truth_file(tmp_path, 252)
        out_path = private_root / "eval.json"
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                ",".join(str(i) for i in range(252)),
                "--binary-actions",
                ",".join("1" for _ in range(252)),
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
                "--output",
                str(out_path),
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(out_path.read_text())
        for token in ("equity_curve", "per_period_pnl", "period_returns", "raw_returns"):
            assert token not in payload, f"{token} leaked into sanitized output"


# ── atlas-harvest ────────────────────────────────────────────────────


class TestAtlasHarvest:
    def test_success_no_auto_accept(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        doc = tmp_path / "doc.txt"
        doc.write_text(
            "Acme Corporation Inc. reported earnings. "
            "Contact john@example.com. Visit https://example.com. "
            "(NYSE: A1B2). CIK #0001234567."
        )
        result = runner.invoke(
            cli_group,
            [
                "atlas-harvest",
                "--private-root",
                str(private_root),
                "--document",
                str(doc),
            ],
        )
        assert result.exit_code == 0, f"{result.exit_code}: {result.stderr}"
        assert "atlas-harvest OK" in result.stdout
        candidates_files = list(private_root.rglob("atlas_candidates.json"))
        assert candidates_files, "atlas_candidates.json was not written"
        latest = max(candidates_files, key=lambda p: p.stat().st_mtime)
        payload = json.loads(latest.read_text())
        assert payload["automatic_acceptance_count"] == 0
        assert payload["registry_mutation_count"] == 0
        for c in payload["candidates"]:
            assert c["is_auto_accepted"] is False

    def test_missing_document_exit_2(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        result = runner.invoke(
            cli_group,
            [
                "atlas-harvest",
                "--private-root",
                str(private_root),
                "--document",
                "/nonexistent/path.txt",
            ],
        )
        assert result.exit_code in (2, 4)


# ── Global privacy/release contract ─────────────────────────────────


class TestPrivacyContract:
    def test_no_private_root_in_env_or_flag_exits_2(
        self,
        runner: CliRunner,
        cli_group,
    ) -> None:
        old = os.environ.pop("FENRIX_PRIVATE_ROOT", None)
        try:
            result = runner.invoke(
                cli_group,
                [
                    "s3-transform",
                    "--variant",
                    "s3b_weekly_features",
                    "--prices",
                    "/nonexistent.json",
                ],
            )
        finally:
            if old is not None:
                os.environ["FENRIX_PRIVATE_ROOT"] = old
        assert result.exit_code in (2, 4)

    def test_direct_export_bypass_attempt_is_sanitized(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """Verify that no per-attack rank data is leaked via the gate output."""
        attacks_path = tmp_path / "attacks_ok.json"
        attacks_path.write_text(_build_complete_evidence_json(rank=200))
        out = private_root / "assess_no_bypass.json"
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(out.read_text())
        assert "evidence" not in payload
        for ev_summary in payload.get("evidence_summary", []):
            for private_field in ("true_source_rank", "score", "percentile_rank"):
                assert private_field not in ev_summary, (
                    f"{private_field} leaked into evidence_summary"
                )
