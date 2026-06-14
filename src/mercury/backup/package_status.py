"""Package status labels for database backup transfer bundles."""


def database_bundle_package_status(
    *,
    source_count: int,
    verified_count: int,
    missing_count: int,
    failed_count: int,
    stale_count: int = 0,
    unknown_freshness_count: int = 0,
) -> str:
    if source_count == 0:
        return "empty"
    complete = (
        verified_count == source_count
        and failed_count == 0
        and missing_count == 0
    )
    if complete:
        if stale_count or unknown_freshness_count:
            return "complete with warnings"
        return "complete"
    return "partial"
