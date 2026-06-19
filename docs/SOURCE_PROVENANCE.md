# Source Provenance Records

This document tracks all code reused from authorized source repositories per AGENTS.md §39-49.

## Format

Each entry records:
- **Source Repository**: Name of source repo
- **Source Path**: Path within source repo
- **Source Commit**: Full git commit SHA
- **Original Responsibility**: What the code did in source
- **Reason for Reuse**: Why not reimplement
- **Dependencies**: Dependencies introduced
- **Modifications**: Changes made
- **Applicable License**: License of source code
- **Attribution**: Required attribution text
- **Tests Added**: New test paths in this repo

---

## Entries

### [Template - No entries yet for M0]

| Field | Value |
|-------|-------|
| Source Repository |  |
| Source Path |  |
| Source Commit |  |
| Original Responsibility |  |
| Reason for Reuse |  |
| Dependencies |  |
| Modifications |  |
| Applicable License |  |
| Attribution |  |
| Tests Added |  |

---

## Investigation Status (M0)

| Repository | Status | Notes |
|------------|--------|-------|
| Project Portfolio Engine | Not yet inspected | Target: checksums, atomic writes, source manifests |
| Bloomberg × Zion | Not yet inspected | Target: SEC parsing utilities |
| Finfluencer Alpha | Not yet inspected | Target: provenance, retry logic |
| Hermes | Not yet inspected | Target: structured logging, retry policies |
| NVIDIA Aggregator | Not applicable for M0 | No model calls in M0 |

---

## Process

Before adapting code from any source repository:

1. Record all fields above in a new entry
2. Verify license compatibility (MIT, Apache-2.0, BSD preferred)
3. Copy only the minimal required utility
4. Add attribution header to copied files
5. Add focused regression tests in `tests/unit/test_reused_*.py`
6. Update this document

Do not copy entire subsystems. Prefer adapting a small utility over importing a full subsystem.