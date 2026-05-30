# Mercury — Claude / agent context

Use **[AGENTS.md](AGENTS.md)** as the primary guide for this repository.

Quick links:

- Safety policy and non-negotiable rules → [AGENTS.md](AGENTS.md#non-negotiable-safety-policy)
- Extension recipes (CLI, backup, DB, tests) → [docs/ai_extension_points.md](docs/ai_extension_points.md)
- Cursor rules → [.cursor/rules/](.cursor/rules/)

```bash
pip install -e ".[mariadb,dev]"
python -m pytest
```

Never weaken production protection. Never commit `config/local.toml`.
