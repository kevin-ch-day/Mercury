# Release Notes

## Guarded recovery milestone

Mercury now provides a proven guarded prod→dev recovery path for Erebus and
ScytaleDroid. The milestone includes artifact-bound restore requirements,
fail-closed privilege preflight, correct handling of MariaDB split `CREATE VIEW`
syntax, and successful live one-pair recovery validation for both projects.

Ordinary prod→dev resets now require the dedicated `[mariadb_restore]`
least-privilege credential lane. It is scoped to approved development targets and
does not fall back to `[mariadb]`, preserving production isolation even if an
ordinary restore path is mis-targeted.
