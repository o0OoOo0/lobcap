# Repository instructions

Read [docs/internal/CODEX_WORKFLOW.md](docs/internal/CODEX_WORKFLOW.md) before
changing runtime code or operating the research VM. Public documentation belongs
in `README.md` and `docs/current/`; development prompts and failure history belong
in `docs/internal/`.

Never commit credentials, raw market-data captures, or machine-specific identifiers.
Run `python scripts/audit_public_repo.py` before pushing public changes.
