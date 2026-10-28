# P01 — Bootstrap, audit, and unit tests

**Record type: reconstructed task specification.** This is not represented as
a verbatim conversation transcript. The repository source is
`docs/internal/CODEX_PROMPTS.md` Prompt 1 plus retained project evidence.

## Objective

- inspect the supplied project archive and detect overwrite conflicts;
- extract only when existing user files are safe;
- read `docs/internal/REMOTE_BOOTSTRAP.md` before executing the bootstrap instructions;
- inspect VM, storage, Python, and active implementation;
- install an isolated Python environment;
- run compile and unit tests;
- report active-code issues and readiness for runtime validation.

## Safety boundary

No exchange runtime test, long-running collector, or systemd deployment.

## Retained evidence

`docs/internal/REMOTE_BOOTSTRAP.md`, `docs/internal/CODEX_PROMPTS.md`, `pyproject.toml`, `requirements.txt`,
and the current test suite. Exact execution timestamps are not preserved in a
dedicated P01 manifest.
