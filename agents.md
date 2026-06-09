# agents.md — Mercury compatibility pointer

Some tools look for a lowercase `agents.md` at repo root.

Use the canonical guidance here:

- [AGENTS.md](./AGENTS.md)
- [.github/copilot-instructions.md](./.github/copilot-instructions.md)

Key expectations:

- Mercury is a Fedora-first CLI for MariaDB backup, verification, restore-check, prod-to-dev sync, Git repo bundles, and transfer manifests/runbooks.
- Preserve the safety rules in `AGENTS.md`.
- Prefer canonical imports under `src/mercury/*` subpackages.
- Run tests before finishing:

```bash
.venv/bin/python -m pytest
```

Do not commit `config/local.toml` or weaken production safety gates.
