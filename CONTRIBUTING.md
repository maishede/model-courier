# Contributing to ModelCourier

ModelCourier is an outbound task relay for small devices and self-hosted model
Workers. Contributions should preserve the versioned REST contracts and keep
heavy model frameworks in optional Worker-side packages.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[test]"
python -m pytest -q
ruff check src tests packages examples
python -m compileall -q src packages examples
```

Before opening a pull request:

- add or update focused tests for behavior changes;
- document protocol or Provider contract changes;
- keep secrets, uploaded media, and local data out of commits;
- explain compatibility and migration impact in the pull request.

Use the issue tracker for reproducible bugs and design proposals. Small fixes
are welcome, but changes to task types, lease semantics, or Provider interfaces
should first be discussed in an issue.
