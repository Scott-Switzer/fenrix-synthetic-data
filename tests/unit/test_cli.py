"""Smoke tests for CLI."""

from pathlib import Path

from click.testing import CliRunner

from fenrix_synthetic.cli import cli


def _create_config(temp_dir: Path, company_id: str = "C001") -> Path:
    """Create a test company config file."""
    config_path = temp_dir / "company.yaml"
    config_path.write_text(f"""\
companies:
  {company_id}:
    source_identity: "TEST"
    data_root: "{temp_dir / "data"}"
    raw_dir: "{temp_dir / "data" / "raw"}"
    bronze_dir: "{temp_dir / "data" / "bronze"}"
""")
    return config_path


class TestCLI:
    """Test CLI commands."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "FENRIX Synthetic Data Worker" in result.output
        assert "ingest" in result.output
        assert "extract" in result.output
        assert "campaign" in result.output

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0, result.output
        assert "0.1.0" in result.output

    def test_hash_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["hash", "test input"])
        assert result.exit_code == 0, result.output
        # Output has the hash on the last line (preceded by JSON log)
        lines = result.output.strip().split("\n")
        assert len(lines[-1]) == 64

    def test_hash_file_command(self, temp_dir: Path):
        runner = CliRunner()
        test_file = temp_dir / "test.txt"
        test_file.write_text("hello")
        result = runner.invoke(cli, ["hash-file", str(test_file)])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().split("\n")
        assert lines[-1] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_hash_file_nonexistent(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["hash-file", "/nonexistent/file.txt"])
        assert result.exit_code != 0

    def test_hash_json_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["hash-json", "--json-input", '{"b": 2, "a": 1}'])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().split("\n")
        assert len(lines[-1]) == 64

    def test_ingest_placeholder(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["ingest", "--company", "C001"])
        assert result.exit_code == 0, result.output
        assert "not yet implemented" in result.output

    def test_extract_requires_fixture_or_live(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["extract", "--company", "C001"])
        assert result.exit_code != 0
        assert "need --fixture" in result.output

    def test_extract_with_fixture(self, temp_dir: Path):
        runner = CliRunner()
        test_fixture = Path(__file__).parent.parent / "fixtures" / "sec"
        test_config = Path(__file__).parent.parent / "fixtures" / "test_company.yaml"
        result = runner.invoke(
            cli,
            [
                "--data-root",
                str(temp_dir),
                "extract",
                "--company",
                "C001",
                "--fixture",
                str(test_fixture),
                "--config",
                str(test_config),
            ],
        )
        print(f"EXIT CODE: {result.exit_code}")
        print(f"OUTPUT: {result.output}")
        assert result.exit_code == 0, result.output
        assert "Extract complete" in result.output

    def test_campaign_dry_run(self, temp_dir: Path):
        config_path = _create_config(temp_dir)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "campaign",
                "--company",
                "C001",
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "validation complete" in result.output.lower()

    def test_campaign_with_options(self, temp_dir: Path):
        config_path = _create_config(temp_dir)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "campaign",
                "--company",
                "C001",
                "--config",
                str(config_path),
                "--no-resume",
                "--continue-on-failure",
            ],
        )
        assert result.exit_code == 0, result.output
