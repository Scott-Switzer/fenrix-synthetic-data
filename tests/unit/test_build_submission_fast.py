from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_builder() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "build_submission_fast.py"
    spec = importlib.util.spec_from_file_location("build_submission_fast", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_submission_fast"] = module
    spec.loader.exec_module(module)
    return module


def write_public_shell(root: Path, builder: ModuleType) -> None:
    (root / "anonymized" / "CL" / "metrics").mkdir(parents=True)
    (root / "qa").mkdir()
    for name in builder.PUBLIC_TOP_LEVEL_FILES:
        (root / name).write_text("safe public content\n", encoding="utf-8")
    (root / "anonymized" / "CL" / "metrics" / "metrics.json").write_text(
        '{"company_id":"COMPANY_001"}\n', encoding="utf-8"
    )
    (root / "qa" / "release_gate.json").write_text('{"artifact_built":true}\n', encoding="utf-8")


def test_deterministic_pseudonyms_are_stable() -> None:
    builder = load_builder()
    first = builder.CompanyContext("CL", 1, "0000021665")
    second = builder.CompanyContext("CL", 1, "0000021665")

    assert first.assign("Colgate-Palmolive Company", "COMPANY") == "COMPANY_001"
    assert first.assign("CL", "TICKER") == "TICKER_001"
    assert first.assign("0000021665", "CIK") == "CIK_001"
    assert first.assign("colgate.com", "DOMAIN") == "DOMAIN_001"
    assert second.assign("colgate.com", "DOMAIN") == "DOMAIN_001"


def test_zip_excludes_private_map_and_source_dirs(tmp_path: Path) -> None:
    builder = load_builder()
    write_public_shell(tmp_path, builder)
    (tmp_path / "private_maps" / "CL").mkdir(parents=True)
    (tmp_path / "private_maps" / "CL" / "identity_map.json").write_text(
        '{"secret":"do-not-ship"}\n', encoding="utf-8"
    )
    (tmp_path / "originals" / "CL").mkdir(parents=True)
    (tmp_path / "originals" / "CL" / "source.txt").write_text("source\n", encoding="utf-8")

    zip_path = builder.package_zip(tmp_path)
    validation = builder.validate_zip(zip_path)

    assert validation.ok
    names = set(__import__("zipfile").ZipFile(zip_path).namelist())
    assert not any(name.startswith("private_maps/") for name in names)
    assert not any(name.startswith("originals/") for name in names)


def test_zip_validation_rejects_api_key_and_local_path(tmp_path: Path) -> None:
    builder = load_builder()
    write_public_shell(tmp_path, builder)
    (tmp_path / "README.md").write_text(
        "safe text nvapi-testsecret123 /Users/example/project\n", encoding="utf-8"
    )

    zip_path = builder.package_zip(tmp_path)
    validation = builder.validate_zip(zip_path)

    assert not validation.ok
    assert validation.api_key_hits
    assert validation.local_path_hits


def test_missing_nvidia_key_continues_as_incomplete(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    builder = load_builder()
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    context = builder.CompanyContext("CL", 1, "0000021665")

    result = builder.run_nvidia_qa("CL", context, ["safe anonymized sample"], "auto")

    assert result["status"] == "INCOMPLETE"
    assert result["decision"] == "NOT_RUN"
