# Backend Project Guidelines

## Purpose

Build maintainable Python APIs for personal applications with a consistent, lightweight stack. Prefer straightforward implementations, predictable deployments, and minimal operational overhead. Do not assume every project is single-user.

## Required stack

- Use Python, FastAPI, SQLAlchemy and Alembic for database migrations.
- Use uv for Python version, environment, dependency, and lockfile management.
- Choose the latest stable mutually compatible versions when initializing a new project, rather than copying versions from reference projects.
- Use SQLite by default. Introduce a client-server database only for concrete requirements (such as high write concurrency, multiple application instances, or database-specific capabilities), after discussing the trade-offs.
- Do not add alternative web frameworks, ORMs, package managers, or infrastructure components without explicit approval.

## Project organization

- Keep application code in `app/`, tests in `tests/`, functional specifications in `specs/`, runtime data in `data/`, and application configuration in `conf/`.
- Keep development, maintenance, and administration utility scripts in `scripts/`.
- Keep deployment-specific scripts and configuration in `deploy/` when deployment tooling is needed; do not mix deployment procedures with general-purpose utility scripts.
- Use `data/` for persistent application files (including SQLite databases); exclude runtime data from version control and never treat it as disposable build output.
- Use `conf/` for application configuration. Version safe examples or defaults where useful, but never commit secrets or private environment-specific settings.
- Organize `app/` into `api/`, `core/`, and `services/` when useful. Do not create empty layers or unnecessary modules.
- Keep HTTP request/response handling separate from business rules and persistence concerns where that separation adds clarity.
- Prefer existing patterns and small, readable modules over speculative abstractions or generic frameworks.

## Quality and testing

- Use Black for formatting, Pylint for linting, mypy in strict mode for static type checking, and pre-commit for relevant automated checks.
- Disable Pylint checks that require docstrings (such as `missing-module-docstring`, `missing-class-docstring`, and `missing-function-docstring`); do not add boilerplate docstrings just to satisfy linting.
- Use a 120-character line length for formatting and linting.
- Prefer pytest for tests; choose a different framework only when there is a concrete project-specific reason.
- Add focused tests for meaningful behavior and bug regressions; avoid retroactive coverage of unrelated code.
- Keep development checks reproducible through uv (for example, `uv run pytest`, `uv run mypy app tests`, and `uv run pylint app tests`), adapting commands to the project's actual layout.
- Run applicable checks and report which were performed and which were not.

## Database and persistent data

- Manage schema changes explicitly with Alembic; do not rely on implicit destructive schema recreation.
- If the latest official release and its applicable Alembic revision are not explicitly identified in the project, STOP and ask the user to confirm the release boundary before making any changes to SQLAlchemy data models, database schemas, or Alembic migrations.
- Preserve all migrations up to and including that official revision. Treat subsequent unreleased migrations and the development data associated with them as disposable: consolidate or rewrite migrations into a clean sequence when changing the schema. Never alter the official migration history or discard data belonging to an official release.
- Development databases and data beyond the official release boundary may be reset or discarded when needed to rebuild the consolidated schema. Preserve data from official releases.
- Make schema migrations reviewable and consider compatibility and recovery before applying them.

## Specifications and documentation

- Maintain concise Markdown specifications in `specs/` for significant features, API contracts, and business rules.
- Update relevant specifications when behavior changes; document observable behavior and constraints rather than narrating implementation details.
- Keep `README.md` focused on setup, development, configuration, and deployment, not agent session history.
- Record project-specific invariants in the project's own `AGENTS.md`.

## Workflow and deployment

- Make the smallest change that fully addresses the task; avoid unrelated refactoring, dependencies, and complexity.
- Follow the project's actual deployment environment rather than assuming FreeBSD, containers, or a particular process manager.
- Summarize changes, checks performed, limitations, and decisions left open.
