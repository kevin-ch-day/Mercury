# Contributing to Mercury

Mercury is a Fedora-first operations CLI for MariaDB backup, verification,
restore-check, prod-to-dev sync, Git repo bundles, and transfer manifests.

Before opening a pull request, read:

- [AGENTS.md](./AGENTS.md)
- [docs/ai_extension_points.md](./docs/ai_extension_points.md)

## Ground rules

- Do not weaken production safety policy.
- Do not add unrelated product scope such as web dashboards, AI features, or workstation bootstrap behavior.
- Do not commit credentials or `config/local.toml`.
- Keep live SQL read-only unless you are in an explicitly gated execution path that already exists.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[mariadb,dev]"
```

## Validation

Run the full test suite before submitting:

```bash
.venv/bin/python -m pytest
```

Useful additional checks:

```bash
.venv/bin/python -m mercury.cli --help
.venv/bin/python -m mercury.cli backup --help
.venv/bin/python -m mercury.cli repo --help
.venv/bin/python -m mercury.cli transfer --help
```

## Pull requests

Use the repository PR template and include:

- what changed
- why it changed
- safety impact
- exact validation commands you ran

If the change touches backup, sync, restore, or execution policy, call that out explicitly in the PR summary.
