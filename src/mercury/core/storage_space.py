"""Configurable free-space policy for Mercury storage roots."""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.storage_roles import DEFAULT_MIN_FREE_BYTES, DEFAULT_MIN_FREE_PERCENT


@dataclass(frozen=True)
class SpacePolicy:
    """Reserve = max(minimum_free_bytes, percent of filesystem capacity)."""

    minimum_free_bytes: int = DEFAULT_MIN_FREE_BYTES
    minimum_free_percent: float = DEFAULT_MIN_FREE_PERCENT

    def required_reserve_bytes(self, *, capacity_bytes: int) -> int:
        if capacity_bytes < 0:
            capacity_bytes = 0
        percent_bytes = int(capacity_bytes * (self.minimum_free_percent / 100.0))
        return max(self.minimum_free_bytes, percent_bytes)

    def required_available_bytes(self, *, capacity_bytes: int, estimated_operation_bytes: int = 0) -> int:
        if estimated_operation_bytes < 0:
            estimated_operation_bytes = 0
        return self.required_reserve_bytes(capacity_bytes=capacity_bytes) + estimated_operation_bytes

    def passes(
        self,
        *,
        capacity_bytes: int,
        available_bytes: int,
        estimated_operation_bytes: int = 0,
    ) -> bool:
        required = self.required_available_bytes(
            capacity_bytes=capacity_bytes,
            estimated_operation_bytes=estimated_operation_bytes,
        )
        return available_bytes >= required


@dataclass(frozen=True)
class SpaceAssessment:
    capacity_bytes: int
    available_bytes: int
    estimated_operation_bytes: int
    required_reserve_bytes: int
    required_available_bytes: int
    passes: bool

    def summary(self) -> str:
        gib = 1024**3
        return (
            f"available {self.available_bytes / gib:.2f} GiB · "
            f"reserve {self.required_reserve_bytes / gib:.2f} GiB · "
            f"need {self.required_available_bytes / gib:.2f} GiB"
            f"{' (ok)' if self.passes else ' (insufficient)'}"
        )


def assess_space(
    policy: SpacePolicy,
    *,
    capacity_bytes: int,
    available_bytes: int,
    estimated_operation_bytes: int = 0,
) -> SpaceAssessment:
    reserve = policy.required_reserve_bytes(capacity_bytes=capacity_bytes)
    required = policy.required_available_bytes(
        capacity_bytes=capacity_bytes,
        estimated_operation_bytes=estimated_operation_bytes,
    )
    return SpaceAssessment(
        capacity_bytes=capacity_bytes,
        available_bytes=available_bytes,
        estimated_operation_bytes=estimated_operation_bytes,
        required_reserve_bytes=reserve,
        required_available_bytes=required,
        passes=available_bytes >= required,
    )
