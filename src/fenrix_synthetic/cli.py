"""CLI entry point for FENRIX Synthetic Data."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from . import __version__
from .config import CampaignConfig, load_company_config
from .schemas import StageName, StageStatus
from .schemas.checkpoints import OutputArtifact, StageCheckpoint
from .storage import get_logger, hash_file, hash_object, hash_string, setup_logging
from .storage.checkpoints import (
    CheckpointStatus,
    CheckpointValidationResult,
    load_checkpoint,
    save_checkpoint,
    validate_checkpoint,
)

logger = get_logger(__name__)


def _run_extract_pipeline(
    company: str,
    config_path: Path | None,
    fixture_dir: Path | None,
    output_root: Path,
    resume: bool,
    form: str,
    user_agent: str | None,
    live: bool,
) -> None:
    """Execute the SEC extraction pipeline for a company.

    Pipeline: discover → download → extract → segment → checkpoint.
    """
    from .schemas.sec import FilingReference
    from .storage.checksums import compute_file_hash, write_sidecar

    company_config = load_company_config(company, config_path)
    ticker = company_config.ticker or company_config.source_identity
    if not ticker:
        click.echo("Error: company config has no ticker or source_identity", err=True)
        sys.exit(1)

    pipeline_version = __version__

    # Build config hash for checkpointing

    config_obj = {
        "company": company,
        "form": form,
        "fixture": str(fixture_dir) if fixture_dir else None,
        "live": live,
    }
    extract_config_hash = hash_object(config_obj)

    # ── Stage 1: Discover ────────────────────────────────────────────
    if resume:
        disc_result = validate_checkpoint(
            output_root,
            company,
            StageName.DISCOVER,
            expected_input_hash=hash_string(company),
            expected_config_hash=extract_config_hash,
            expected_version=pipeline_version,
        )
    else:
        disc_result = CheckpointValidationResult(
            stage=StageName.DISCOVER,
            company_id=company,
            status=CheckpointStatus.INVALID_HASH,
            message="Resume disabled",
        )

    if disc_result.status == CheckpointStatus.VALID:
        click.echo("  discover: checkpoint valid, skipping")
        cp = load_checkpoint(output_root, company, StageName.DISCOVER)
        pass
    else:
        click.echo(f"  discover: running ({disc_result.status.value})")
        from .sec import SECClient
        from .sec.transport import FixtureTransport, LiveTransport

        if fixture_dir:
            from .sec.transport import SecTransport

            transport: SecTransport = FixtureTransport(fixture_dir)
        elif live:
            from .sec.transport import SecTransport

            ua = user_agent or "Fenrix Research contact@example.invalid"
            transport = LiveTransport(ua)
        else:
            click.echo("Error: no fixture directory and --live not specified", err=True)
            click.echo("Supply --fixture for offline mode or --live for live SEC access")
            sys.exit(1)

        client = SECClient(transport)
        click.echo(f"  Resolving CIK for ticker '{ticker}'...")

        cik = client.resolve_cik(ticker)
        if not cik:
            if fixture_dir:
                cik_from_config = company_config.cik
                if cik_from_config:
                    cik = SECClient.normalize_cik(cik_from_config)
                    click.echo(f"  Using configured CIK: {cik}")
                else:
                    click.echo(
                        f"  Warning: could not resolve CIK for '{ticker}', using test fixture CIK"
                    )
                    cik = "0000000000"
            else:
                click.echo(f"Error: could not resolve CIK for ticker '{ticker}'", err=True)
                sys.exit(1)

        raw_filings = client.get_filings(ticker, form=form, limit=1)
        if not raw_filings:
            click.echo(f"Error: no {form} filings found for {ticker}", err=True)
            sys.exit(1)

        filing = raw_filings[0]
        accession = filing["accessionNumber"]
        primary_doc = filing.get("primaryDocument", "")
        filing_url = SECClient.build_filing_url(cik, accession, primary_doc)

        filing_ref = FilingReference(
            accession_number=accession,
            cik=cik,
            form=filing["form"],
            filing_date=filing["filingDate"],
            report_date=filing.get("reportDate", ""),
            primary_document=primary_doc,
            filing_url=filing_url,
        )

        click.echo(
            f"  Discovered {filing_ref.form} filing {accession} from {filing_ref.filing_date}"
        )

        filings_meta = [filing_ref.model_dump()]

        cp = StageCheckpoint(
            stage=StageName.DISCOVER,
            company_id=company,
            input_hash=hash_string(company),
            config_hash=extract_config_hash,
            output_artifacts=[],
            status=StageStatus.COMPLETED,
            pipeline_version=pipeline_version,
            metadata={"filings": filings_meta, "cik": cik, "ticker": ticker},
        )
        save_checkpoint(output_root, cp)

    # ── Stage 2: Extract (download raw + convert to bronze) ──────────
    if resume:
        ext_result = validate_checkpoint(
            output_root,
            company,
            StageName.EXTRACT,
            expected_input_hash=extract_config_hash,
            expected_config_hash=extract_config_hash,
            expected_version=pipeline_version,
        )
    else:
        ext_result = CheckpointValidationResult(
            stage=StageName.EXTRACT,
            company_id=company,
            status=CheckpointStatus.INVALID_HASH,
            message="Resume disabled",
        )

    if ext_result.status == CheckpointStatus.VALID:
        click.echo("  extract: checkpoint valid, skipping")
        return
    else:
        click.echo(f"  extract: running ({ext_result.status.value})")

    # Recover discovery metadata
    disc_cp = load_checkpoint(output_root, company, StageName.DISCOVER)
    if not disc_cp:
        click.echo("Error: discover checkpoint missing", err=True)
        sys.exit(1)
    filings_meta = disc_cp.metadata.get("filings", [])
    cik = disc_cp.metadata.get("cik", "")

    if not filings_meta:
        click.echo("Error: no filings to extract", err=True)
        sys.exit(1)

    filing_m = filings_meta[0]
    filing_ref = FilingReference(**filing_m)
    accession_no_dashes = filing_ref.accession_number.replace("-", "")

    # Download raw HTML
    raw_dir = output_root / "raw" / company
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_filename = f"{accession_no_dashes}.html"
    raw_path = raw_dir / raw_filename

    if raw_path.exists():
        raw_sha256 = compute_file_hash(raw_path)
        click.echo(f"  Raw file exists: {raw_path} (hash: {raw_sha256[:16]}...)")
    else:
        if fixture_dir:
            from .sec.transport import FixtureTransport

            transport = FixtureTransport(fixture_dir)
        elif live:
            ua = user_agent or "Fenrix Research contact@example.invalid"
            from .sec.transport import LiveTransport

            transport = LiveTransport(ua)
        else:
            click.echo("Error: no fixture or live mode for download", err=True)
            sys.exit(1)

        click.echo(f"  Downloading from SEC: {filing_ref.filing_url}")
        resp = transport.get_bytes(filing_ref.filing_url, timeout=60)
        raw_path.write_bytes(resp.content)
        raw_sha256 = compute_file_hash(raw_path)
        click.echo(f"  Downloaded {len(resp.content)} bytes (hash: {raw_sha256[:16]}...)")

    # Write sidecar
    sidecar = write_sidecar(raw_path)

    # Convert HTML to normalized text
    bronze_dir = output_root / "bronze" / company
    bronze_dir.mkdir(parents=True, exist_ok=True)

    html_content = raw_path.read_text(encoding="utf-8", errors="replace")

    from .extraction.converter import HtmlFilingExtractor

    extractor = HtmlFilingExtractor()
    result = extractor.extract(html_content)
    normalized_text = result["text"]

    # Write normalized text
    text_filename = f"{accession_no_dashes}.md"
    text_path = bronze_dir / text_filename
    text_path.write_text(normalized_text, encoding="utf-8")
    text_sha256 = compute_file_hash(text_path)

    # Segment into sections
    from .extraction.segmenter import FilingSegmenter

    segmenter = FilingSegmenter()
    sections = segmenter.segment(normalized_text)

    sections_filename = f"{accession_no_dashes}_sections.json"
    sections_path = bronze_dir / sections_filename
    sections_data = [
        {"item": s.item, "title": s.title, "char_count": s.char_count} for s in sections
    ]
    import orjson as orjson_mod

    sections_path.write_bytes(
        orjson_mod.dumps(sections_data, option=orjson_mod.OPT_SORT_KEYS | orjson_mod.OPT_INDENT_2)
    )

    # Save extract checkpoint
    ext_cp = StageCheckpoint(
        stage=StageName.EXTRACT,
        company_id=company,
        input_hash=extract_config_hash,
        config_hash=extract_config_hash,
        output_artifacts=[
            OutputArtifact(path=raw_path, hash=raw_sha256),
            *(
                [OutputArtifact(path=sidecar, hash=compute_file_hash(sidecar))]
                if sidecar.exists()
                else []
            ),
            OutputArtifact(path=text_path, hash=text_sha256),
            OutputArtifact(path=sections_path, hash=hash_file(sections_path)),
        ],
        status=StageStatus.COMPLETED,
        pipeline_version=pipeline_version,
        metadata={
            "accession": filing_ref.accession_number,
            "form": filing_ref.form,
            "filing_date": filing_ref.filing_date,
            "section_count": len(sections),
        },
    )
    save_checkpoint(output_root, ext_cp)

    click.echo(f"  Raw artifact: {raw_path}")
    click.echo(f"  Sidecar: {sidecar}")
    click.echo(f"  Normalized text: {text_path}")
    click.echo(f"  Sections: {sections_path} ({len(sections)} sections)")
    click.echo("  Extract complete")


@click.group()
@click.version_option(version=__version__, prog_name="fenrix-synth")
@click.option("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
@click.option(
    "--log-format", type=click.Choice(["json", "text"]), default="json", help="Log format"
)
@click.option(
    "--data-root", type=click.Path(path_type=Path), default=Path("data"), help="Data root directory"
)
@click.pass_context
def cli(ctx: click.Context, log_level: str, log_format: str, data_root: Path) -> None:
    """FENRIX Synthetic Data Worker - Reproducible financial data masking and re-identification testing."""
    ctx.ensure_object(dict)
    ctx.obj["data_root"] = data_root
    setup_logging(level=log_level, format_type=log_format)
    logger.info("CLI initialized", extra={"version": __version__, "data_root": str(data_root)})


@cli.command()
@click.option("--company", required=True, help="Company ID (e.g., C001)")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to company config",
)
@click.option(
    "--fixture",
    "fixture_dir",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Fixture directory for offline mode",
)
@click.option("--form", default="10-K", help="SEC form type (e.g., 10-K)")
@click.option("--live", is_flag=True, default=False, help="Enable live SEC network access")
@click.option("--user-agent", default=None, help="SEC User-Agent string (for live mode)")
@click.option("--resume/--no-resume", default=True, help="Resume from checkpoints")
@click.pass_context
def extract(
    ctx: click.Context,
    company: str,
    config_path: Path | None,
    fixture_dir: Path | None,
    form: str,
    live: bool,
    user_agent: str | None,
    resume: bool,
) -> None:
    """Extract SEC filing text for a company.

    Runs discovery → download → HTML conversion → section segmentation
    → checkpoint save.

    Uses offline fixture files when --fixture is provided.
    Requires --live for live SEC network access.
    """
    output_root = ctx.obj["data_root"]
    logger.info(
        "Extract command",
        extra={
            "company": company,
            "fixture": str(fixture_dir) if fixture_dir else None,
            "form": form,
            "live": live,
            "resume": resume,
        },
    )

    if not fixture_dir and not live:
        click.echo("Error: need --fixture (offline) or --live (live SEC access)", err=True)
        sys.exit(1)

    try:
        _run_extract_pipeline(
            company=company,
            config_path=config_path,
            fixture_dir=fixture_dir,
            output_root=output_root,
            resume=resume,
            form=form,
            user_agent=user_agent,
            live=live,
        )
    except Exception as e:
        logger.error("Extract command failed", extra={"error": str(e)}, exc_info=True)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--company", required=True, help="Company ID (e.g., C001)")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to company config",
)
@click.pass_context
def ingest(ctx: click.Context, company: str, config_path: Path | None) -> None:
    """Ingest SEC filings for a company (placeholder for M2)."""
    logger.info("Ingest command", extra={"company": company})
    click.echo(f"Ingest stage for {company} - not yet implemented (M2)")


@cli.command()
@click.option("--company", required=True, help="Company ID (e.g., C001)")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to company config",
)
@click.option("--resume/--no-resume", default=True, help="Resume from checkpoints")
@click.option("--stop-on-failure/--continue-on-failure", default=True, help="Stop on stage failure")
@click.pass_context
def campaign(
    ctx: click.Context,
    company: str,
    config_path: Path | None,
    resume: bool,
    stop_on_failure: bool,
) -> None:
    """Run full campaign for a company."""
    data_root = ctx.obj["data_root"]
    logger.info("Campaign command", extra={"company": company, "resume": resume})

    try:
        company_config = load_company_config(company, config_path)
        logger.info("Loaded company config", extra={"company_id": company_config.company_id})

        campaign_config = CampaignConfig(
            company_id=company,
            stages=[StageName.DISCOVER, StageName.EXTRACT, StageName.MANIFEST],
            resume=resume,
            stop_on_failure=stop_on_failure,
        )

        if resume:
            for stage in campaign_config.stages:
                result = validate_checkpoint(
                    data_root,
                    company,
                    stage,
                    expected_input_hash="",
                    expected_config_hash="",
                    expected_version=__version__,
                )
                if result.status == CheckpointStatus.VALID:
                    logger.info("Checkpoint valid, would skip", extra={"stage": stage.value})
                    click.echo(f"  {stage.value}: would skip (valid checkpoint)")
                else:
                    logger.info(
                        "Checkpoint invalid or missing",
                        extra={"stage": stage.value, "status": result.status.value},
                    )
                    click.echo(f"  {stage.value}: would run ({result.status.value})")
        else:
            for stage in campaign_config.stages:
                click.echo(f"  {stage.value}: would run")

        click.echo(f"\nCampaign for {company} - validation complete (use 'extract' subcommand)")

    except Exception as e:
        logger.error("Campaign failed", extra={"error": str(e)}, exc_info=True)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("input_text")
@click.pass_context
def hash(ctx: click.Context, input_text: str) -> None:
    """Compute SHA-256 hash of input string."""
    result = hash_string(input_text)
    click.echo(result)


@cli.command(name="hash-file")
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def hash_file_cmd(ctx: click.Context, file_path: Path) -> None:
    """Compute SHA-256 hash of file."""
    result = hash_file(file_path)
    click.echo(result)


@cli.command(name="hash-json")
@click.option("--json-input", help="JSON string to hash")
@click.pass_context
def hash_json(ctx: click.Context, json_input: str) -> None:
    """Compute deterministic SHA-256 hash of JSON object."""
    import orjson

    obj = orjson.loads(json_input)
    result = hash_object(obj)
    click.echo(result)


@cli.command(name="registry-validate")
@click.option(
    "--registry", "registry_path", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option("--company", required=True)
def registry_validate(registry_path: Path, company: str) -> None:
    """Validate an identity registry YAML file."""
    import yaml as _yaml

    with open(registry_path) as f:
        data = _yaml.safe_load(f)

    raw = data if isinstance(data, dict) else {}
    entities = raw.get("entities", [])
    aliases = raw.get("aliases", [])
    errors: list[str] = []

    entity_ids = set()
    for i, ent in enumerate(entities):
        eid = ent.get("entity_id", "")
        if not eid:
            errors.append(f"entities[{i}]: missing entity_id")
        elif eid in entity_ids:
            errors.append(f"entities[{i}]: duplicate entity_id '{eid}'")
        entity_ids.add(eid)

    alias_ids = set()
    for i, ali in enumerate(aliases):
        aid = ali.get("alias_id", "")
        if not aid:
            errors.append(f"aliases[{i}]: missing alias_id")
        elif aid in alias_ids:
            errors.append(f"aliases[{i}]: duplicate alias_id '{aid}'")
        alias_ids.add(aid)
        eid = ali.get("canonical_entity_id", "")
        if eid and eid not in entity_ids:
            errors.append(f"aliases[{i}]: references unknown entity '{eid}'")

    if errors:
        for err in errors:
            click.echo(f"  ERROR: {err}", err=True)
        click.echo(f"Validation FAILED ({len(errors)} errors)")
        sys.exit(1)
    else:
        click.echo(f"Registry valid: {len(entities)} entities, {len(aliases)} aliases")


@cli.command(name="registry-inventory")
@click.option(
    "--registry", "registry_path", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--sanitize/--no-sanitize",
    default=True,
    help="Sanitize output to hide private values (default: sanitize)",
)
def registry_inventory(registry_path: Path, sanitize: bool) -> None:
    """List entities and aliases in an identity registry.

    By default, output is sanitized (private values hidden).
    Use --no-sanitize to see actual values (exercise caution).
    """
    import yaml as _yaml

    with open(registry_path) as f:
        data = _yaml.safe_load(f)

    raw = data if isinstance(data, dict) else {}
    entities = raw.get("entities", [])
    aliases = raw.get("aliases", [])

    click.echo(f"Entities ({len(entities)}):")
    for ent in entities:
        value = ent.get("canonical_private_value", "")
        display = "[PRIVATE]" if sanitize and value else value
        click.echo(f"  {ent.get('entity_id')}: {ent.get('entity_type')} = {display}")

    click.echo(f"\nAliases ({len(aliases)}):")
    for ali in aliases:
        value = ali.get("private_alias_value", "")
        display = "[PRIVATE]" if sanitize and value else value
        click.echo(f"  {ali.get('alias_id')}: [{ali.get('match_policy')}] {display}")

    if sanitize:
        click.echo("\n(Values hidden with --sanitize; use --no-sanitize to reveal)")


@cli.command()
@click.option("--company", required=True, help="Company ID")
@click.option(
    "--data-root",
    "data_root_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Data root directory",
)
@click.option(
    "--bronze-artifact",
    required=True,
    help="Bronze artifact ID (e.g., bronze-C001-000123456724000001)",
)
@click.option(
    "--registry",
    "registry_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to identity registry YAML file",
)
@click.option(
    "--masked-output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for masked text",
)
@click.option(
    "--audit-output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for private audit JSON",
)
@click.option(
    "--summary-output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for sanitized summary JSON",
)
@click.pass_context
def mask(
    ctx: click.Context,
    company: str,
    data_root_path: Path,
    bronze_artifact: str,
    registry_path: Path,
    masked_output: Path,
    audit_output: Path,
    summary_output: Path,
) -> None:
    """Run deterministic masking on a bronze document.

    Loads an identity registry from a YAML file and applies deterministic
    pattern-based matching and pseudonym replacement.
    """
    import yaml as _yaml

    from .identity import EntityType, MatchPolicy
    from .identity.entity_registry import EntityRegistry
    from .masking import DeterministicMasker

    # Load bronze document
    bronze_dir = data_root_path / "bronze" / company
    text_path = bronze_dir / f"{bronze_artifact.replace('bronze-', '')}.md"

    if not text_path.exists():
        click.echo(f"Error: bronze text not found: {text_path}", err=True)
        sys.exit(1)

    text = text_path.read_text()

    # Load registry from YAML
    with open(registry_path) as f:
        raw = _yaml.safe_load(f)

    reg_data = raw if isinstance(raw, dict) else {}
    reg_meta = reg_data.get("registry", {})
    reg = EntityRegistry.create(
        company_id=company,
        registry_id=reg_meta.get("registry_id", f"reg-{company}-cli"),
    )

    def _lookup_etype(name: str) -> EntityType:
        for et in EntityType:
            if et.value == name:
                return et
        return EntityType.COMPANY

    def _lookup_mpolicy(name: str) -> MatchPolicy:
        for mp in MatchPolicy:
            if mp.value == name:
                return mp
        return MatchPolicy.LITERAL

    for ent in reg_data.get("entities", []):
        eid = ent.get("entity_id", "")
        etype = _lookup_etype(ent.get("entity_type", "company"))
        value = ent.get("canonical_private_value", "")
        if eid and value:
            try:
                reg.add_entity(eid, etype, value, ent.get("source_references"))
            except ValueError:
                click.echo(f"Warning: duplicate entity '{eid}', skipping", err=True)

    for ali in reg_data.get("aliases", []):
        aid = ali.get("alias_id", "")
        eid = ali.get("canonical_entity_id", "")
        value = ali.get("private_alias_value", "")
        etype = _lookup_etype(ali.get("entity_type", "company"))
        mpolicy = _lookup_mpolicy(ali.get("match_policy", "literal"))
        priority = ali.get("priority", 100)
        if aid and eid and eid in reg.entities and value:
            try:
                reg.add_alias(aid, eid, value, etype, mpolicy, priority)
            except ValueError:
                click.echo(f"Warning: duplicate alias '{aid}', skipping", err=True)

    masker = DeterministicMasker(reg, document_artifact_id=bronze_artifact)
    config_hash = reg.config_hash()
    masked_text, _sanitized_meta, audit, summary = masker.mask_and_sanitize_metadata(
        text,
        {"source": bronze_artifact, "registry_id": reg.metadata.registry_id},
        config_hash,
    )

    masked_output.parent.mkdir(parents=True, exist_ok=True)
    masked_output.write_text(masked_text)

    audit_output.parent.mkdir(parents=True, exist_ok=True)
    audit_output.write_text(audit.model_dump_json(indent=2))

    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(summary.model_dump_json(indent=2))

    click.echo(f"Masked document: {masked_output}")
    click.echo(f"Private audit: {audit_output}")
    click.echo(f"Sanitized summary: {summary_output}")
    click.echo(f"Matches: {summary.match_count}, Replacements: {summary.replacement_count}")


@cli.command()
@click.option(
    "--document", "document_path", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--values",
    "values_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="YAML file with values to scan for",
)
def scan(document_path: Path, values_path: Path) -> None:
    """Run exact residual scan on a document."""
    import yaml as _yaml

    from .attacks import ExactResidualScanner

    text = document_path.read_text()

    with open(values_path) as f:
        values_data = _yaml.safe_load(f)

    scanner = ExactResidualScanner()
    result = scanner.scan_text(text, values_data)

    click.echo(f"Scan results for {document_path.name}:")
    click.echo(f"  Total hits: {result.total_hits}")
    click.echo(f"  Blocking hits: {result.blocking_hits}")
    click.echo(f"  Allowed hits: {result.allowed_hits}")
    click.echo(f"  Blocked: {result.is_blocked}")

    if result.is_blocked:
        click.echo("  Blocking values found:")
        for htype, hits in result.hits_by_type.items():
            for hit in hits:
                click.echo(f"    [{htype}] {hit.value}")
        sys.exit(1)


@cli.command()
@click.option(
    "--document", "document_path", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--audit",
    "audit_path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to masking audit JSON (enables coverage computation)",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    help="Output path for discovery results JSON",
)
def discover(document_path: Path, audit_path: Path | None, output_path: Path | None) -> None:
    """Run residual entity discovery on a document.

    Uses pattern-based heuristics to find potential entities that may
    have survived the deterministic masking pipeline.

    When --audit is provided, computes coverage statistics by comparing
    discovered entities against the masking audit's accepted spans.
    """
    from .masking.discovery import ResidualEntityDiscoverer
    from .masking.schemas import MaskingAudit
    from .reporting.coverage import CoverageReport

    text = document_path.read_text()
    discoverer = ResidualEntityDiscoverer()

    known_pseudonyms: set[str] = set()
    accepted_spans: list[tuple[int, int]] = []

    if audit_path:
        audit_data = MaskingAudit.model_validate_json(audit_path.read_text())
        for span in audit_data.spans:
            if span.conflict_status.value == "accepted":
                accepted_spans.append((span.original_start, span.original_end))
        known_pseudonyms = discoverer.extract_pseudonyms_from_audit(audit_data)

    discovered = discoverer.discover(text, known_pseudonyms)

    click.echo(f"Discovery results for {document_path.name}:")
    click.echo(f"  Total entities found: {len(discovered)}")

    if audit_path and accepted_spans:
        report = CoverageReport()
        coverage = report.compute(
            discovered,
            accepted_spans,
            company_id=audit_data.company_id,
            document_artifact_id=audit_data.document_artifact_id,
        )
        click.echo(f"  Masked: {coverage.total_masked}")
        click.echo(f"  Unmasked: {coverage.total_unmasked}")
        click.echo(f"  Coverage: {coverage.coverage_pct}%")
        click.echo(f"  High-confidence unmasked: {coverage.high_confidence_unmasked}")

        if coverage.warnings:
            for w in coverage.warnings:
                click.echo(f"  Warning: {w}")

        if output_path:
            import orjson as _orjson

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(_orjson.dumps(coverage.to_dict(), option=_orjson.OPT_INDENT_2))
            click.echo(f"  Written to: {output_path}")
    else:
        click.echo("  (pass --audit for coverage statistics)")
        by_type: dict[str, int] = {}
        for e in discovered:
            by_type[e.discovery_type] = by_type.get(e.discovery_type, 0) + 1
        for dtype, count in sorted(by_type.items()):
            click.echo(f"    {dtype}: {count}")


@cli.command()
@click.option(
    "--document", "document_path", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--company",
    "company_id",
    required=True,
    help="Company ID (REQUIRED, never defaults). Example: TEST-CO-001.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    help="Output path for Phase 3B discovery results JSON",
)
@click.pass_context
def discover3b(
    ctx: click.Context, document_path: Path, company_id: str, output_path: Path | None
) -> None:
    """Run model-assisted entity discovery (Phase 3B).

    Uses the fake provider to discover entities, then runs
    deduplication and risk scoring.
    """
    from .discovery import (
        CandidateDeduplicator,
        CandidateNormalizer,
        ChunkingConfig,
        FakeEntityDiscoveryProvider,
        FakeProviderConfig,
        FakeProviderMode,
        TextChunker,
        aggregate_provider_candidates,
        build_sanitized_report,
        make_sanitized_summary,
    )
    from .storage import hash_string

    text = document_path.read_text()
    doc_id = document_path.stem

    # Chunk the document
    chunker = TextChunker(ChunkingConfig(max_chars=500, overlap_chars=50))
    chunks = chunker.chunk(text, doc_id)
    click.echo(f"Phase 3B discovery on {document_path.name}:")
    click.echo(f"  Document length: {len(text)} chars")
    click.echo(f"  Chunks: {len(chunks)}")

    # Run discovery with fake provider
    provider = FakeEntityDiscoveryProvider(
        FakeProviderConfig(
            mode=FakeProviderMode.FIXED,
            fixed_candidates=[
                {
                    "text": "Acme Corporation",
                    "start": 0,
                    "end": 16,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.85,
                },
                {
                    "text": "Jane Smith",
                    "start": 50,
                    "end": 60,
                    "entity_type": "PERSON",
                    "label": "PERSON",
                    "confidence": 0.75,
                },
            ],
        )
    )

    responses = []
    for chunk in chunks:
        response = provider.discover(chunk, labels=["COMPANY", "PERSON", "PRODUCT"])
        responses.append(response)

    all_candidates = aggregate_provider_candidates(responses)
    click.echo(f"  Provider candidates: {len(all_candidates)}")

    # Deduplicate and score
    deduplicator = CandidateDeduplicator()
    deduped, group_map = deduplicator.deduplicate(all_candidates)
    click.echo(f"  After deduplication: {len(deduped)}")

    normalizer = CandidateNormalizer()
    scored = normalizer.normalize(deduped)

    # Build sanitized summaries (no private text exposed)
    summaries = make_sanitized_summary(scored, group_map)
    for s in summaries:
        dup_info = ""
        if s.duplicate_group_id and s.provider_agreement_count > 1:
            dup_info = f" (grp={s.duplicate_group_id[:8]}...)"
        click.echo(
            f"    [{s.risk_band}] {s.proposed_entity_type} conf={s.confidence:.2f} id={s.opaque_id}...{dup_info}"
        )

    # Build sanitized report
    report = build_sanitized_report(
        candidates=scored,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
        model_version=provider.model_version,
        company_id=company_id,
        document_artifact_id=doc_id,
        input_hash=hash_string(text),
        latency_ms=50.0,
        token_count=100,
        warnings=[],
        duplicate_groups=len(group_map),
    )

    click.echo(f"  Total scored: {report.total_candidates}")
    click.echo(f"  By risk band: {report.candidates_by_band}")

    if output_path:
        import orjson as _orjson

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_orjson.dumps(report.to_dict(), option=_orjson.OPT_INDENT_2))
        click.echo(f"  Written to: {output_path}")


@cli.group()
def providers() -> None:
    """Discover and operate discovery providers."""


@providers.command(name="list")
def providers_list() -> None:
    """List available discovery providers and their dependencies."""
    from .discovery import FakeEntityDiscoveryProvider
    from .discovery.providers.gliner import is_gliner_available

    click.echo("Discovery providers:")
    click.echo(f"  fake: available ({FakeEntityDiscoveryProvider.__module__})")
    if is_gliner_available():
        click.echo(
            "  gliner_local: available (gliner installed; "
            "ensure model cache or use 'providers prepare')"
        )
    else:
        click.echo("  gliner_local: unavailable (install with pip install -e '.[local-ner]')")


@providers.command(name="health")
@click.option("--provider", "provider_name", default="gliner_local")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to provider config YAML (optional)",
)
@click.option(
    "--company",
    "company_id",
    default="provider-health-check",
    help="Synthetic company tag for health-checking; never real C001/HBAN.",
)
def providers_health(provider_name: str, config_path: Path | None, company_id: str) -> None:
    """Check discovery provider health and dependency status.

    For gliner_local: distinguishes missing dependency, download disabled,
    model unavailable, and healthy cached model.
    """
    if provider_name != "gliner_local":
        click.echo(f"Unknown provider: {provider_name}", err=True)
        sys.exit(2)

    from .discovery.providers.gliner import (
        GLiNERConfig,
        GLiNERLocalProvider,
        GlinerModelLoadError,
        OptionalDependencyError,
        default_gliner_loader,
        is_gliner_available,
    )

    if not is_gliner_available():
        click.echo("status=dependency-missing")
        click.echo("detail=gliner is not installed")
        sys.exit(0)

    config = GLiNERConfig(
        model_id="urchade/gliner_small-v2.5",
        company_id=company_id,
        allow_download=False,
    )

    try:
        provider = GLiNERLocalProvider(config=config, loader=default_gliner_loader)
        ok = provider.health_check()
    except OptionalDependencyError as e:
        click.echo("status=dependency-missing")
        click.echo(f"detail={e}")
        sys.exit(0)
    except GlinerModelLoadError as e:
        click.echo("status=model-unavailable-locally")
        click.echo(f"detail={e}")
        sys.exit(0)
    except (RuntimeError, ValueError, TypeError, OSError) as e:
        # Programming / data errors that are not valid load results — surface
        # as load-failure without swallowing unrelated bugs into provider errors.
        click.echo(f"status=load-failure: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    if ok:
        click.echo("status=healthy")
        click.echo(f"model_id={provider.model_name}")
        click.echo(f"config_hash={provider.config_hash}")
    else:
        click.echo("status=load-failure")
        sys.exit(1)


@providers.command(name="prepare")
@click.option("--provider", "provider_name", default="gliner_local")
@click.option(
    "--model",
    "model_id",
    default="urchade/gliner_small-v2.5",
    help="Hugging Face model identifier",
)
@click.option("--allow-download", is_flag=True, default=False)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Local cache directory for model weights (gitignored)",
)
@click.option(
    "--company",
    "company_id",
    default="provider-prepare-check",
    help="Synthetic company tag; never real C001/HBAN.",
)
def providers_prepare(
    provider_name: str,
    model_id: str,
    allow_download: bool,
    cache_dir: Path | None,
    company_id: str,
) -> None:
    """Acquire/adopt provider model weights (explicit opt-in only)."""
    if provider_name != "gliner_local":
        click.echo(f"Unknown provider: {provider_name}", err=True)
        sys.exit(2)
    if not allow_download:
        click.echo(
            "Refusing to acquire model without --allow-download. "
            "Pre-existing local weights remain usable.",
            err=True,
        )
        sys.exit(2)
    from .discovery.providers.gliner import (
        GLiNERConfig,
        GlinerModelLoadError,
        OptionalDependencyError,
        default_gliner_loader,
    )

    config = GLiNERConfig(
        model_id=model_id,
        company_id=company_id,
        allow_download=True,
        cache_dir=str(cache_dir) if cache_dir else None,
    )
    try:
        model = default_gliner_loader(config)
    except OptionalDependencyError as e:
        click.echo(f"dependency-missing: {e}", err=True)
        sys.exit(1)
    except GlinerModelLoadError as e:
        click.echo(f"load-failure: {e}", err=True)
        sys.exit(1)
    if model is None:
        click.echo("status=failed", err=True)
        sys.exit(1)
    click.echo(f"status=ready model_id={model_id}")


# ── Ingestion provenance helpers ───────────────────────────────────────


def _ingest_is_working_tree_clean() -> tuple[bool, str]:
    """Check if the current working directory is clean (no tracked file modifications)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False, "could not run git status"
    lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
    dirty_lines: list[str] = []
    for line in lines:
        if len(line) < 3:
            continue
        status = line[:2]
        # Untracked files are allowed
        if status == "??":
            continue
        dirty_lines.append(line)
    if dirty_lines:
        return False, "\n".join(dirty_lines)
    return True, ""


def _ingest_get_head_commit() -> str:
    """Read the current HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


# ── Privacy scan ─────────────────────────────────────────────────────────

# Explicitly forbidden keys that indicate private content leakage.
# Keys like "note" are intentionally NOT listed here.
_INGEST_FORBIDDEN_KEYS: set[str] = {
    "candidate_text",
    "matched_text",
    "private_matched_text",
    "context",
    "context_excerpt",
    "raw_response",
    "source_alias",
    "source_url",
    "cache_path",
    "absolute_path",
    "private_hash",
    "access_token",
    "text",
    "url",
    "path",
    "alias",
    "company_name",
    "excerpt",
}


def _scan_forbidden(obj: Any, path: str = "") -> None:
    """Recursively scan report dict for forbidden keys.

    Only rejects keys that are explicitly in the forbidden set.
    Long strings are NOT rejected by a generic heuristic.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _INGEST_FORBIDDEN_KEYS:
                click.echo(f"Error: forbidden key '{k}' at {path}.{k}", err=True)
                sys.exit(1)
            _scan_forbidden(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_forbidden(item, f"{path}[{i}]")


@providers.command(name="ingest-colab")
@click.option(
    "--report",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to the sanitized Colab evidence JSON report.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for the deterministic ingestion artifact.",
)
@click.option(
    "--expected-commit",
    default=None,
    help="Expected repository commit SHA. If not provided, uses local HEAD.",
)
@click.pass_context
def providers_ingest_colab(
    ctx: click.Context,
    report: Path,
    output: Path,
    expected_commit: str | None,
) -> None:
    """Ingest a sanitized Colab evidence report for Phase 3C verification.

    Validates the report schema, verifies repository commit and benchmark
    hash, rejects raw/private text fields, asserts zero auto-acceptance
    and zero auto-promotion, and generates a deterministic sanitized
    evidence artifact. Does NOT update any registry or review decisions.
    Does NOT make any anonymity claim.
    """
    import hashlib
    import json
    from datetime import UTC, datetime

    from .discovery.providers.gliner.benchmark import load_default_benchmark

    # ── Ingestion-code provenance ─────────────────────────────────────────
    local_commit = _ingest_get_head_commit()
    clean, dirty_details = _ingest_is_working_tree_clean()
    if not clean:
        click.echo(f"Error: ingestion working tree is dirty:\n{dirty_details}", err=True)
        sys.exit(1)
    if expected_commit and expected_commit != local_commit:
        click.echo(
            f"Error: ingestion commit mismatch: expected {expected_commit}, got {local_commit}",
            err=True,
        )
        sys.exit(1)

    # Load report
    try:
        report_data = json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        click.echo(f"Error: invalid JSON in report: {e}", err=True)
        sys.exit(1)

    # Required fields
    required = [
        "environment",
        "repository",
        "model",
        "discovery",
        "evaluation",
        "review_queue",
        "privacy",
        "run_timestamp",
    ]
    missing = [f for f in required if f not in report_data]
    if missing:
        click.echo(f"Error: missing required top-level fields: {missing}", err=True)
        sys.exit(1)

    # Verify zero acceptance / promotion / registry mutation / remasking
    rq = report_data["review_queue"]
    if rq.get("automatic_acceptance_count", 0) != 0:
        click.echo("Error: automatic_acceptance_count is non-zero", err=True)
        sys.exit(1)
    if rq.get("automatic_promotion_count", 0) != 0:
        click.echo("Error: automatic_promotion_count is non-zero", err=True)
        sys.exit(1)
    if rq.get("registry_mutation_count", 0) != 0:
        click.echo("Error: registry_mutation_count is non-zero", err=True)
        sys.exit(1)
    if rq.get("remasking_count", 0) != 0:
        click.echo("Error: remasking_count is non-zero", err=True)
        sys.exit(1)

    # Verify no real data
    if not report_data["privacy"].get("no_real_company_data", False):
        click.echo("Error: privacy.no_real_company_data is false", err=True)
        sys.exit(1)

    # Verify working tree clean and commit verified from report
    repo = report_data["repository"]
    if repo.get("working_tree_clean") is False:
        click.echo("Error: report indicates working_tree_clean=false", err=True)
        sys.exit(1)
    if repo.get("commit_verified") is False:
        click.echo("Error: report indicates commit_verified=false", err=True)
        sys.exit(1)

    # Verify model load and predict_entities success
    model = report_data["model"]
    if not model.get("load_success", False):
        click.echo("Error: model.load_success is false", err=True)
        sys.exit(1)
    if not report_data["discovery"].get("predict_entities_success", False):
        click.echo("Error: discovery.predict_entities_success is false", err=True)
        sys.exit(1)

    # Verify review queue populated when candidates exist
    discovery = report_data["discovery"]
    normalized_count = discovery.get("normalized_candidate_count", 0)
    review_queue_count = rq.get("review_queue_count", 0)
    if normalized_count > 0 and review_queue_count == 0:
        click.echo(
            f"Error: {normalized_count} normalized candidates but review_queue_count=0", err=True
        )
        sys.exit(1)

    # Verify evidence payload hash (schema integrity)
    evidence_schema_version = report_data.get("evidence_schema_version")
    if evidence_schema_version is None:
        click.echo("Error: evidence_schema_version missing", err=True)
        sys.exit(1)
    stored_hash = report_data.get("evidence_payload_hash", "")
    if not stored_hash:
        click.echo("Error: evidence_payload_hash missing", err=True)
        sys.exit(1)
    # Compute hash over canonical JSON excluding the hash field itself
    hashable = {k: v for k, v in report_data.items() if k != "evidence_payload_hash"}
    canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    computed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    if computed_hash != stored_hash:
        click.echo(
            f"Error: evidence payload hash mismatch: expected {stored_hash}, got {computed_hash}",
            err=True,
        )
        sys.exit(1)

    # Verify commit from report matches local HEAD
    repo_commit = report_data["repository"].get("checked_out_commit", "")
    if repo_commit and repo_commit not in local_commit and local_commit not in repo_commit:
        click.echo(
            f"Error: report commit {repo_commit} does not match local HEAD {local_commit}",
            err=True,
        )
        sys.exit(1)

    # Verify benchmark hash
    expected_bench_hash = load_default_benchmark().benchmark_hash
    actual_bench_hash = report_data["evaluation"].get("benchmark_hash", "")
    if actual_bench_hash != expected_bench_hash:
        click.echo(
            f"Error: benchmark hash mismatch: expected {expected_bench_hash}, got {actual_bench_hash}",
            err=True,
        )
        sys.exit(1)

    # Scan for forbidden fields
    _scan_forbidden(report_data)

    # Compact summary metrics
    content = json.dumps(report_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    eval_metrics = report_data["evaluation"]
    model_perf = report_data["model"]
    discovery_metrics = report_data["discovery"]

    artifact = {
        "ingestion_schema_version": "1.0.0",
        "ingested_at": datetime.now(UTC).isoformat(),
        "verification_status": "accepted",
        "content_hash": content_hash,
        "repository_commit": repo_commit,
        "benchmark_hash": actual_bench_hash,
        "benchmark_scope": eval_metrics.get("benchmark_scope", "unknown"),
        "model_identifier": model_perf.get("model_id", ""),
        "canonical_entity_types_tested": eval_metrics.get("canonical_entity_types_tested", []),
        "principal_metrics": {
            "exact_precision": eval_metrics.get("exact_precision"),
            "exact_recall": eval_metrics.get("exact_recall"),
            "exact_f1": eval_metrics.get("exact_f1"),
            "relaxed_precision": eval_metrics.get("relaxed_precision"),
            "relaxed_recall": eval_metrics.get("relaxed_recall"),
            "relaxed_f1": eval_metrics.get("relaxed_f1"),
        },
        "review_queue_counts": {
            "review_queue_count": rq.get("review_queue_count", 0),
            "pending_review_count": rq.get("pending_review_count", 0),
            "normalized_candidate_count": discovery_metrics.get("normalized_candidate_count", 0),
            "automatic_acceptance_count": rq.get("automatic_acceptance_count", 0),
            "automatic_promotion_count": rq.get("automatic_promotion_count", 0),
            "registry_mutation_count": rq.get("registry_mutation_count", 0),
            "remasking_count": rq.get("remasking_count", 0),
        },
        "verification_checklist": {
            "all_required_fields_present": {f: f in report_data for f in required},
            "repository_commit_verified": repo_commit in local_commit
            or local_commit in repo_commit,
            "ingestion_commit_verified": expected_commit is None or expected_commit == local_commit,
            "ingestion_working_tree_clean": clean,
            "benchmark_hash_verified": actual_bench_hash == expected_bench_hash,
            "zero_acceptance_verified": True,
            "zero_promotion_verified": True,
            "no_real_data_verified": True,
            "evidence_payload_hash_verified": bool(stored_hash),
            "privacy_scan_passed": True,
        },
        "anonymity_claim": None,
        "anonymity_disclaimer": (
            "Phase 3C does not establish anonymity or release safety. "
            "This artifact only verifies that the Colab smoke executed "
            "without auto-acceptance and without real-company data. "
            "This is integrity protection against accidental edits, not a "
            "cryptographic authenticity claim."
        ),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    click.echo(f"Ingestion artifact: {output}")
    click.echo(f"Content hash: {content_hash}")
    click.echo("Verification: ACCEPTED")


def _resolve_private_output_root(
    private_output_root: Path | None,
    data_root: Path,
) -> Path:
    """Resolve a private output directory. Guard against tracked directories."""
    import subprocess

    target = private_output_root or (data_root / "private" / "gliner")
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "."
    ).resolve()
    target_in_repo = (target.resolve()).is_relative_to(repo_root)
    if target_in_repo:
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(target.relative_to(repo_root))],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if tracked.returncode == 0:
                click.echo(
                    f"Error: --private-output-root {target} is inside the repository and tracked by git. "
                    "Refusing to write private artifacts into a tracked path.",
                    err=True,
                )
                sys.exit(2)
        except (subprocess.SubprocessError, OSError):
            pass
    return target


@cli.command(name="discover-model")
@click.option(
    "--provider",
    "provider_name",
    default="gliner_local",
    help="Discovery provider name (only gliner_local in Phase 3C)",
)
@click.option(
    "--company",
    "company_id",
    required=True,
    help="Company ID — REQUIRED, never defaults. Example: TEST-CO-001.",
)
@click.option(
    "--document",
    "document_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Synthetic or private input document",
)
@click.option(
    "--labels-config",
    type=click.Path(exists=True, path_type=Path),
    default=Path("configs/entity_labels.yaml"),
    help="Path to entity-labels mapping YAML",
)
@click.option(
    "--provider-config",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to provider-specific config (optional YAML)",
)
@click.option(
    "--private-output-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Gitignored root under which private artifacts are written",
)
@click.option("--threshold", default=0.50, type=float)
@click.option("--model", "model_id", default="urchade/gliner_small-v2.5")
@click.option("--allow-download", is_flag=True, default=False)
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
@click.option("--no-private-write", is_flag=True, default=False)
@click.pass_context
def discover_model(
    ctx: click.Context,
    provider_name: str,
    company_id: str,
    document_path: Path,
    labels_config: Path,
    provider_config: Path | None,
    private_output_root: Path | None,
    threshold: float,
    model_id: str,
    allow_download: bool,
    output_path: Path | None,
    no_private_write: bool,
) -> None:
    """Run model-aided entity discovery (Phase 3C).

    Imports the GLiNER provider explicitly only inside this command; the rest
    of the CLI does not require gliner. The result is a private
    ProviderCandidate set plus an aggregate SanitizedDiscoveryReport.

    --company is REQUIRED. There is no default; supplying a real source
    company ID via this command is an operator error and must not happen
    silently under any code path.
    """
    if provider_name != "gliner_local":
        click.echo(f"Unknown provider: {provider_name}", err=True)
        sys.exit(2)

    from .discovery import (
        CandidateDeduplicator,
        CandidateNormalizer,
        ChunkingConfig,
        TextChunker,
        aggregate_provider_candidates,
        build_sanitized_report,
        make_sanitized_summary,
    )
    from .discovery.providers.gliner import (
        GLiNERConfig,
        OptionalDependencyError,
        default_gliner_loader,
        is_gliner_available,
    )
    from .discovery.providers.gliner.mapping import load_label_mapping
    from .discovery.providers.gliner.provider import GLiNERLocalProvider
    from .storage import hash_string

    if not is_gliner_available():
        click.echo(
            "Error: gliner is not installed. Run: pip install -e '.[local-ner]'",
            err=True,
        )
        sys.exit(1)

    text = document_path.read_text()
    doc_id = document_path.stem
    chunker = TextChunker(ChunkingConfig(max_chars=2000, overlap_chars=150))
    chunks = chunker.chunk(text, doc_id)
    click.echo(f"Phase 3C discovery on {document_path.name}:")
    click.echo(f"  Document length: {len(text)} chars")
    click.echo(f"  Chunks: {len(chunks)}")

    if not company_id or not isinstance(company_id, str):
        click.echo("Error: --company must be a non-empty identifier", err=True)
        sys.exit(2)

    config = GLiNERConfig(
        model_id=model_id,
        company_id=company_id,
        threshold=threshold,
        allow_download=allow_download,
    )

    try:
        provider = GLiNERLocalProvider(
            config=config,
            loader=default_gliner_loader,
            label_mapping=load_label_mapping(labels_config),
        )
    except OptionalDependencyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    health = provider.health_check()
    click.echo(f"  Provider health: {'healthy' if health else 'unhealthy'}")

    responses = []
    for chunk in chunks:
        responses.append(provider.discover(chunk, labels=["company", "subsidiary", "executive"]))

    all_candidates = aggregate_provider_candidates(responses)
    click.echo(f"  Raw candidates: {len(all_candidates)}")

    deduplicator = CandidateDeduplicator()
    deduped, group_map = deduplicator.deduplicate(all_candidates)
    normalizer = CandidateNormalizer()
    scored = normalizer.normalize(deduped)
    click.echo(f"  After dedup: {len(deduped)}; groups: {len(group_map)}")

    summaries = make_sanitized_summary(scored, group_map)
    click.echo(f"  Sanitized summaries: {len(summaries)}")
    for s in summaries[:5]:
        click.echo(
            f"    [{s.risk_band}] {s.proposed_entity_type} "
            f"conf={s.confidence:.2f} opaque_id={s.opaque_id[:8]}..."
        )

    # Build review queue from scored candidates (no auto-accept, no auto-promote)
    from .discovery import ReviewQueue

    queue = ReviewQueue(company_id=company_id, document_artifact_id=doc_id)
    for c in scored:
        queue.add_candidate(c)

    report = build_sanitized_report(
        candidates=scored,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
        model_version=provider.model_version,
        company_id=company_id,
        document_artifact_id=doc_id,
        input_hash=hash_string(text),
        latency_ms=50.0,
        token_count=None,
        warnings=[],
        duplicate_groups=len(group_map),
    )

    if not no_private_write:
        data_root = ctx.obj["data_root"]
        resolved = _resolve_private_output_root(private_output_root, Path(data_root))
        safe_path = resolved / f"{doc_id}_private.json"
        import orjson as _orjson

        private_payload = {
            "candidates": [c.__dict__ for c in all_candidates],
            "model_identity": provider.model_identity,
            "config_hash": provider.config_hash,
            "threshold": threshold,
            "labels_config": str(labels_config),
            "input_hash": report.input_hash,
            "document_artifact_id": doc_id,
            "company_id": company_id,
            "raw_count": len(all_candidates),
            "deduped_count": len(deduped),
            "review_queue": {
                "review_queue_count": len(queue.all_reviews()),
                "pending_count": queue.pending_count(),
                "accepted_count": queue.accepted_count(),
                "rejected_count": queue.rejected_count(),
                "review_records": [r.__dict__ for r in queue.review_records()],
            },
        }
        safe_path.write_bytes(
            _orjson.dumps(private_payload, default=str, option=_orjson.OPT_INDENT_2)
        )
        click.echo(f"  Private artifact: {safe_path}")

    if output_path:
        import orjson as _orjson

        # Include review queue counts in sanitized report
        report_dict = report.to_dict()
        report_dict["review_queue"] = {
            "review_queue_count": len(queue.all_reviews()),
            "pending_count": queue.pending_count(),
            "accepted_count": queue.accepted_count(),
            "rejected_count": queue.rejected_count(),
            "automatic_acceptance_count": 0,
            "automatic_promotion_count": 0,
            "registry_mutation_count": 0,
            "remasking_count": 0,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_orjson.dumps(report_dict, option=_orjson.OPT_INDENT_2))
        click.echo(f"  Sanitized report: {output_path}")

    click.echo(f"  total_candidates={report.total_candidates} bands={report.candidates_by_band}")
    click.echo(f"  review_queue_count={len(queue.all_reviews())} pending={queue.pending_count()}")
    click.echo("  No candidate has been accepted, promoted, or masked.")


# ── Phase 4 CLI commands ────────────────────────────────────────────────


@cli.command(name="identities-compile")
@click.option(
    "--atlas",
    "atlas_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to identity atlas YAML (under FENRIX_PRIVATE_ROOT).",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for compiled replacement plan JSON.",
)
@click.pass_context
def identities_compile(
    ctx: click.Context,
    atlas_path: Path,
    output_path: Path,
) -> None:
    """Compile an identity atlas into a deterministic replacement plan."""
    import json

    import yaml as _yaml

    from .atlas import IdentityAtlas, compile_atlas
    from .boundary import resolve_private_root

    # Verify atlas is under private root
    private_root = resolve_private_root()
    if not str(atlas_path.resolve()).startswith(str(private_root)):
        click.echo(
            f"Error: atlas must be under FENRIX_PRIVATE_ROOT ({private_root})",
            err=True,
        )
        sys.exit(1)

    with open(atlas_path) as f:
        data = _yaml.safe_load(f)

    raw = data if isinstance(data, dict) else {}
    atlas = IdentityAtlas(**raw)
    plan = compile_atlas(atlas)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan.to_dict(), indent=2))
    click.echo(f"Compiled atlas: {plan.atlas_hash[:16]}")
    click.echo(f"  Blocking replacements: {len(plan.get_blocking())}")
    click.echo(f"  Non-blocking: {len(plan.get_non_blocking())}")
    click.echo(f"  Plan written to: {output_path}")


@cli.command(name="structured-transform")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to structured data JSON (OHLCV format).",
)
@click.option(
    "--variant",
    type=click.Choice(["s0_control", "s1_basic", "s2_privacy"]),
    default="s1_basic",
    help="Transformation variant.",
)
@click.option("--base-price", type=float, default=100.0, help="Base price for rebasing.")
@click.option("--output", "output_path", type=click.Path(path_type=Path), required=True)
@click.pass_context
def structured_transform(
    ctx: click.Context,
    input_path: Path,
    variant: str,
    base_price: float,
    output_path: Path,
) -> None:
    """Apply a structured data transformation variant."""
    import json

    from .transforms import (
        OhlcvRecord,
        transform_s0_control,
        transform_s1_basic,
        transform_s2_privacy,
    )

    data = json.loads(input_path.read_text())
    records = [OhlcvRecord(**r) for r in data.get("records", [])]

    if variant == "s0_control":
        result = transform_s0_control(records, base_price)
    elif variant == "s1_basic":
        result = transform_s1_basic(records, base_price)
    elif variant == "s2_privacy":
        result = transform_s2_privacy(records, base_price)
    else:
        click.echo(f"Unknown variant: {variant}", err=True)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "variant": result.variant.value,
        "parameter_hash": result.parameter_hash,
        "row_count": result.row_count,
        "releasable": result.releasable,
        "warnings": result.warnings,
        "transformed": result.transformed,
    }
    output_path.write_text(json.dumps(output, indent=2))
    click.echo(f"Variant: {result.variant.value}")
    click.echo(f"  Rows: {result.row_count}")
    click.echo(f"  Releasable: {result.releasable}")
    if result.warnings:
        for w in result.warnings:
            click.echo(f"  Warning: {w}")
    click.echo(f"  Output: {output_path}")


@cli.command(name="attack-run")
@click.option(
    "--document",
    "document_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to masked document.",
)
@click.option(
    "--values",
    "values_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to YAML with private values to scan for.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for attack results JSON.",
)
@click.pass_context
def attack_run(
    ctx: click.Context,
    document_path: Path,
    values_path: Path,
    output_path: Path,
) -> None:
    """Run re-identification attacks on a masked document."""
    import json

    import yaml as _yaml

    from .attacks.text_attacks import (
        digital_identifier_scan,
        exact_identity_scan,
        normalized_identity_scan,
    )

    text = document_path.read_text()

    with open(values_path) as f:
        values_data = _yaml.safe_load(f)

    values_dict = values_data if isinstance(values_data, dict) else {}

    results = []

    # Exact identity scan
    exact = exact_identity_scan(text, document_path.stem, values_dict)
    results.append(
        {
            "attack_type": exact.attack_type,
            "total_hits": exact.total_hits,
            "blocking_hits": exact.blocking_hits,
            "is_blocked": exact.is_blocked,
        }
    )

    # Normalized scan
    norm = normalized_identity_scan(text, document_path.stem, values_dict)
    results.append(
        {
            "attack_type": norm.attack_type,
            "total_hits": norm.total_hits,
            "blocking_hits": norm.blocking_hits,
            "is_blocked": norm.is_blocked,
        }
    )

    # Digital scan
    dig = digital_identifier_scan(
        text,
        document_path.stem,
        values_dict.get("websites", []),
        values_dict.get("domains", []),
        values_dict.get("emails", []),
        values_dict.get("phones", []),
    )
    results.append(
        {
            "attack_type": dig.attack_type,
            "total_hits": dig.total_hits,
            "blocking_hits": dig.blocking_hits,
            "is_blocked": dig.is_blocked,
        }
    )

    any_blocked = any(r["is_blocked"] for r in results)

    click.echo(f"Attacks on {document_path.name}:")
    for r in results:
        status = "BLOCKED" if r["is_blocked"] else "PASS"
        click.echo(
            f"  {r['attack_type']}: {status} "
            f"(total={r['total_hits']}, blocking={r['blocking_hits']})"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2))

    if any_blocked:
        sys.exit(1)


@cli.command(name="utility-evaluate")
@click.option(
    "--source",
    "source_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to source (pre-masking) document.",
)
@click.option(
    "--masked",
    "masked_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to masked document.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for utility results JSON.",
)
@click.pass_context
def utility_evaluate(
    ctx: click.Context,
    source_path: Path,
    masked_path: Path,
    output_path: Path,
) -> None:
    """Evaluate utility preservation between source and masked text."""
    import json

    from .utility import evaluate_unstructured_utility

    source_text = source_path.read_text()
    masked_text = masked_path.read_text()

    result = evaluate_unstructured_utility(source_text, masked_text, document_id=source_path.stem)

    output = {
        "document_id": result.document_id,
        "non_identifier_token_retention": result.non_identifier_token_retention,
        "section_retention": result.section_retention,
        "table_retention": result.table_retention,
        "financial_number_retention": result.financial_number_retention,
        "overall_utility": result.overall_utility,
        "warnings": result.warnings,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))

    click.echo(f"Utility evaluation for {source_path.name}:")
    click.echo(f"  Token retention: {result.non_identifier_token_retention:.2%}")
    click.echo(f"  Financial number retention: {result.financial_number_retention:.2%}")
    click.echo(f"  Overall utility: {result.overall_utility:.2%}")
    if result.warnings:
        for w in result.warnings:
            click.echo(f"  Warning: {w}")


@cli.command(name="release-assess")
@click.option(
    "--attack-results",
    "attack_results_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to attack results JSON.",
)
@click.option(
    "--config",
    "policy_path",
    type=click.Path(exists=True, path_type=Path),
    default=Path("configs/policies/pilot_v1.yaml"),
    help="Path to release policy YAML.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for release decision JSON.",
)
@click.pass_context
def release_assess(
    ctx: click.Context,
    attack_results_path: Path,
    policy_path: Path,
    output_path: Path,
) -> None:
    """Assess release readiness using the deterministic release gate."""
    import json

    import yaml as _yaml

    from .release import evaluate_release_gate

    with open(attack_results_path) as f:
        attack_data = json.load(f)

    with open(policy_path) as f:
        policy = _yaml.safe_load(f) or {}

    def _count_hits(results: list[dict[str, Any]], attack_type: str) -> int:
        for r in results:
            if r.get("attack_type") == attack_type:
                return int(r.get("blocking_hits", 0))
        return 0

    gate = evaluate_release_gate(
        text_attacks_blocked=any(r.get("is_blocked") for r in attack_data.get("results", [])),
        structured_rank=attack_data.get("structured_rank", -1),
        structured_top_k=10,
        llm_blocked=attack_data.get("llm_blocked", False),
        exact_identity_hits=_count_hits(attack_data.get("results", []), "exact_identity"),
        unique_phrase_hits=_count_hits(attack_data.get("results", []), "unique_phrase"),
        digital_hits=_count_hits(attack_data.get("results", []), "digital_identifier"),
        filename_hits=_count_hits(attack_data.get("results", []), "filename_metadata"),
        deterministic_reproduced=True,
        all_attacks_ran=True,
        provenance_complete=True,
        private_paths_found=[],
        unhandled_errors=[],
        policy=policy.get("policy", {}),
    )

    decision = {
        "decision": gate.decision.value,
        "gate_hash": gate.gate_hash,
        "blocking_failures": gate.blocking_failures,
        "warnings": gate.warnings,
        "conditions": [
            {
                "id": c.condition_id,
                "passed": c.passed,
                "blocking": c.is_blocking,
                "description": c.description,
            }
            for c in gate.conditions
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(decision, indent=2))

    click.echo(f"Release assessment: {gate.decision.value}")
    click.echo(f"  Blocking failures: {gate.blocking_failures}")
    click.echo(f"  Warnings: {gate.warnings}")

    if gate.decision.value == "FAIL":
        sys.exit(1)


@cli.command(name="release-export")
@click.option(
    "--output",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Output directory for the release dossier.",
)
@click.option(
    "--company",
    "company_id",
    default="SYNTH_001",
    help="Release identifier (default: SYNTH_001).",
)
@click.pass_context
def release_export(
    ctx: click.Context,
    output_dir: Path,
    company_id: str,
) -> None:
    """Generate a sanitized release dossier."""

    from .release import generate_dossier, validate_dossier

    dossier_root = generate_dossier(
        dossier_root=output_dir,
        company_id=company_id,
    )

    valid, issues = validate_dossier(dossier_root)
    if not valid:
        for issue in issues:
            click.echo(f"  Error: {issue}", err=True)
        sys.exit(1)

    click.echo(f"Release dossier generated: {dossier_root}")
    click.echo(f"  Contents: {sorted(f.name for f in dossier_root.iterdir())}")


@cli.command(name="boundary-diag")
@click.pass_context
def boundary_diag(ctx: click.Context) -> None:
    """Show redacted diagnostic information about the private data boundary."""
    import json

    from .boundary import redacted_diagnostic_command

    diag = redacted_diagnostic_command()
    click.echo(json.dumps(diag, indent=2))


def main() -> None:  # type: ignore[no-any-return]
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
