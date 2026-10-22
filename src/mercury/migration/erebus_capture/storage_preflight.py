"""Injected, fail-closed storage checks for source-capture previews."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ErebusCaptureStorageIdentity

from mercury.core.storage_roles import DEFAULT_PRIMARY_LABEL, primary_storage_label

EXPECTED_LABEL = DEFAULT_PRIMARY_LABEL  # compatibility export; validation resolves config dynamically
EXPECTED_UUID = "715f29a9-2671-477b-8c8d-515d190addb9"
EXPECTED_MOUNT = "/mnt/MERCURY_DATA"


@dataclass(frozen=True)
class StorageFacts:
    partition: str
    parent: str
    fstype: str
    label: str
    uuid: str
    mount_path: str
    mount_options: str
    free_bytes: int
    source_host: bool
    writer_enabled: bool
    active_operations: tuple[str, ...] = ()
    ambiguous: bool = False


def validate_storage(facts: StorageFacts, *, minimum_free_bytes: int = 1) -> ErebusCaptureStorageIdentity:
    errors = []
    if facts.ambiguous: errors.append("ambiguous block device")
    if facts.uuid != EXPECTED_UUID: errors.append("UUID mismatch")
    if facts.label != primary_storage_label(): errors.append("label mismatch")
    if facts.mount_path != EXPECTED_MOUNT: errors.append("canonical mount mismatch")
    if not facts.fstype: errors.append("filesystem missing")
    if facts.free_bytes < minimum_free_bytes: errors.append("insufficient free space")
    if not facts.source_host: errors.append("destination role cannot authorize source capture")
    if not facts.writer_enabled: errors.append("source writer is disabled")
    if facts.active_operations: errors.append("active operation: " + ", ".join(facts.active_operations))
    if errors: raise ValueError("; ".join(errors))
    return ErebusCaptureStorageIdentity(facts.label, facts.uuid, facts.mount_path, facts.free_bytes, facts.source_host)
