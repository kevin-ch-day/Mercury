"""Backward compatibility — use mercury.env.probe."""

from mercury.env.probe import EnvProbeResult, format_policy_summary, probe_environment

__all__ = ["EnvProbeResult", "format_policy_summary", "probe_environment"]
