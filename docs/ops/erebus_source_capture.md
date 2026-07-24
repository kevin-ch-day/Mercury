# Erebus source-capture preview

This workflow prepares a governed, read-only receipt for a replacement Erebus
source capture. It does not copy source files, write a capture, generate a
package, start MariaDB, modify Phase 3B, or detach the Mercury HDD. Capture
execution remains unavailable pending Phase B review.

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

## States and later execution gate

New receipts are `READY`. Only `READY` may become `EXECUTION_STARTED`; only
that state may become `CONSUMED`. A drift check atomically marks a receipt
`INVALIDATED`; explicit rejection can record `REFUSED`. State corruption,
reuse, and concurrent begin attempts fail closed. Before any future writer,
the complete source, storage, recovery, Phase 3B, intake, checksum, state, and
final-path checks must be repeated. No capture temporary directory is created
by preview or revalidation.

## Interactive route

Open **Workstation migration → Source capture → Capture Erebus source**. The
screen has only **Preview capture** and **Review previews**. Entering it does
not create a preview. Preview capture asks for every explicit CLI input,
including the reviewed storage-facts receipt, and invokes the same
`create_preview()` service as the CLI. Review lists exact named preview
directories; it never selects a "latest" receipt.

Troubleshoot the emitted stable refusal (for example
`INVALID_PREVIEW_ID`, `FINAL_CAPTURE_EXISTS`, `EXTERNAL_IDENTITY_MISMATCH`,
`PREVIEW_CHECKSUM_MISMATCH`, or `PREVIEW_COMPONENT_MISMATCH`) rather than
altering a published preview in place.
