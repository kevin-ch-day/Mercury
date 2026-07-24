# Erebus source-capture preview and synthetic execute

This workflow prepares a governed, read-only receipt for a replacement Erebus
source capture. Preview does not copy source files, write a capture, generate a
package, start MariaDB, modify Phase 3B, or detach the Mercury HDD. Production
capture execution remains locked; only explicitly synthetic test contexts may
write a capture.

## Request, context, and validator order

The operator supplies an exact preview ID, capture ID, repository, expected
commit/tree, Phase 3B run ID, `maintenance.py` SHA-256, recovery receipt,
Phase 3B evidence root, intake contract, control root, and a reviewed
`StorageFacts` JSON receipt. There is no `latest` selection. Preview IDs reject
empty, traversal, separator, and duplicate values; an existing final capture
path also refuses the request.

The service validates the repository is clean `main`, matches HEAD/tree and
`origin/main`, and that the recovered maintenance module is present, tracked,
not ignored, and hash-pinned. It validates the Mercury label, UUID, canonical
mount, filesystem, free space, source-host/writer state, and absence of active
operations. Recovery receipt and sidecar, Phase 3B evidence and pinned backups,
and intake schema/allowlist/sidecar are mandatory. The service first builds an
in-memory `PreviewPayload` in this order: source/maintenance identity, storage,
recovery receipt, Phase 3B evidence, and intake contract. Only then does the
single publisher create a temporary receipt directory.

## Command

```bash
./run.sh migration capture-erebus-source preview \
  --preview-id <id> --repo <repo> --capture-id <id> \
  --expected-commit <sha> --expected-tree <tree> \
  --phase3b-run-id 20260722T055400Z_phase3b \
  --maintenance-sha256 <sha256> --recovery-receipt <receipt.json> \
  --phase3b-root <evidence-root> --intake-contract <contract.json> \
  --control-root <control-root> --storage-facts <reviewed-facts.json>
```

`--storage-facts` makes this dependency explicit and testable; the command does
not silently inspect or mutate the mounted HDD. A refusal exits nonzero and
prints stable reason codes.

Production execute is available as a refusal route only:

```bash
./run.sh migration capture-erebus-source execute \
  --preview-id <exact-ready-id> --repo <repo> \
  --recovery-receipt <receipt.json> --phase3b-root <evidence-root> \
  --intake-contract <contract.json> --control-root <control-root> \
  --storage-facts <reviewed-facts.json>
```

There is no CLI flag, environment variable, or preview field that enables
synthetic capture from the operator CLI.

## Durable receipt

The publisher writes a private temporary directory beneath
`validation/previews/erebus/`, fsyncs receipt files and directories, verifies
the receipt, then atomically renames it to the requested preview ID. Failed
publication leaves no final directory; temporary directories are retained only
when an operating-system failure prevents their removal for diagnosis.

The final directory contains exactly:

- `capture_preview.json`, `capture_preview.sha256`, `source_identity.json`
- `storage_identity.json`, `recovery_identity.json`, `phase3b_identity.json`
- `intake_identity.json`, `intended_members.json`, `preflight_report.json`
- `safety_decision.json`, `preview_state.json`

The checksum map deterministically covers every JSON receipt file (not the
checksum map itself). Loading refuses missing,
unexpected, non-regular, malformed, checksum-mismatched, or cross-file
inconsistent content.

## States and execution gate

New receipts are `READY`. Only `READY` may become `EXECUTION_STARTED`; only
that state may become `CONSUMED`. A drift check atomically marks a receipt
`INVALIDATED`; explicit refusal after a writer error records `REFUSED`. State
corruption, reuse, and concurrent begin attempts fail closed. Before any
writer, the complete source, storage, recovery, Phase 3B, intake, checksum,
state, and final-path checks must be repeated. No capture temporary directory
is created by preview or revalidation.

## Synthetic Phase B execution

The implementation has a capture writer used by synthetic tests and by
authorized real execution. Synthetic tests set
`CaptureContext.allow_synthetic_execution`. Real execution requires a host-local
authorization receipt (`mercury.erebus_capture.execution_authorization.v1`) that
pins an exact preview ID, capture ID, Mercury/Erebus commits, confirmation
phrase `AUTHORIZE EREBUS CAPTURE EXECUTE`, optional expiry, and optional approved
full-suite exceptions. Ordinary CLI/menu contexts without that receipt remain
locked (`EXECUTION_NOT_AUTHORIZED`).

```bash
./run.sh migration capture-erebus-source review-preview \
  --preview-id <exact-ready-id> --control-root <control-root>

./run.sh migration capture-erebus-source execute \
  --preview-id <exact-ready-id> --repo <repo> \
  --recovery-receipt <receipt.json> --phase3b-root <evidence-root> \
  --intake-contract <contract.json> --control-root <control-root> \
  --storage-facts <reviewed-facts.json> \
  --authorization-receipt <host-local-auth.json>
```

Without `--authorization-receipt`, execute refuses. There is no environment
variable or preview field that enables capture.

A synthetic READY preview is revalidated, reserved, written under
`validation/erebus/.<capture-id>.tmp-*`, and only then atomically renamed after
Git evidence, an explicit-main bundle, independent reconstruction, governed
identity artifacts, member/prohibited-content checks, and a checksum manifest
verify. Failure removes the exact temporary directory and never publishes a
final capture; success consumes the preview.

Stable writer/execute reason codes include:

- `EXECUTION_NOT_AUTHORIZED`
- `AUTHORIZATION_*` receipt refusals
- `VALIDATION_RUNNER_REQUIRED`
- `VALIDATION_FAILED:<step>`
- `FULL_SUITE_*` policy refusals
- `RECONSTRUCTION_MISMATCH`
- `RECONSTRUCTION_VALIDATION_FAILED`
- `PROHIBITED_CONTENT`
- `MANIFEST_INVALID`
- `FINAL_CAPTURE_EXISTS`
- `PREVIEW_NOT_READY`

The resulting capture includes Git and validation evidence, recovery/intake and
Phase 3B linkage, reconstruction receipts, `checksums.sha256`, its verification
receipt, `manifest_receipt.json`, `capture_summary.json`, `CAPTURE_REPORT.md`,
and a supersession record for the historical incomplete capture. Package
validation accepts only an explicit `CAPTURE_VERIFIED` capture with matching
commit/tree, manifest, reconstruction, recovery hash, and Phase 3B linkage.
Tampered status, authority, identity, reconstruction, receipt, recovery, Phase
3B linkage, supersession, members, or checksums are refused.

## Phase C — package authority

Destination package preview and create share
`assess_erebus_capture_for_package()` and classify each capture as:

- `PACKAGE_AUTHORITY` — `CAPTURE_VERIFIED`, active, validator passes
- `HISTORICAL_REFERENCE` — preserved for display only; create refuses
- `MISSING` — fail closed in production preview/create
- `REFUSED` — claimed verified but failed validation or identity pins

Production create never promotes historical references and never creates a
package from a missing path. The only exception is an injected call argument
`allow_synthetic_missing_capture_fixture=True` used by hermetic tests. That
flag is not exposed by CLI, environment, preview receipts, or package requests.

`read_erebus_capture_identity()` accepts canonical top-level `commit`/`tree` or
legacy `repository.commit`/`repository.tree`. Canonical fields are
authoritative when present; conflicting dual shapes refuse.

## Interactive route

Open **Workstation migration → Source capture → Capture Erebus source**. Enter
the control root first. The screen always offers **Preview capture** and
**Review previews**. **Create approved capture** appears only while at least
one exact preview loads as READY. Entering the menu does not create a preview.
Preview capture asks for every explicit CLI input, including the reviewed
storage-facts receipt, and invokes the same `create_preview()` service as the
CLI. Review lists exact named preview directories; it never selects a "latest"
receipt. The execute action still uses a production context and refuses with
`EXECUTION_NOT_AUTHORIZED`.

Troubleshoot the emitted stable refusal (for example
`INVALID_PREVIEW_ID`, `FINAL_CAPTURE_EXISTS`, `EXTERNAL_IDENTITY_MISMATCH`,
`PREVIEW_CHECKSUM_MISMATCH`, `PREVIEW_COMPONENT_MISMATCH`,
`EXECUTION_NOT_AUTHORIZED`, or the writer codes above) rather than altering a
published preview in place.
