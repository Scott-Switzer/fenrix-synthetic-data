"""CLI entry point for FENRIX Synthetic Data."""

import sys
from pathlib import Path

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


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
