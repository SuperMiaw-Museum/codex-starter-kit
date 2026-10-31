# Python CLI Project Guidelines

## Purpose

Build maintainable Python command-line tools with a consistent, lightweight stack. Prefer straightforward implementations, predictable execution, and minimal operational overhead.

## Required stack

- Use uv for Python version, environment, dependency, and lockfile management, and run tools and checks through `uv run`. Commit the project's `uv.lock` for reproducible dependencies.
- Choose the latest stable mutually compatible dependencies when initializing a new project, rather than copying versions from reference projects. Choose the Python version according to the project's requirements and compatibility constraints.
- A database is optional; add one only when the tool needs it. When needed, use SQLite by default and require SQLAlchemy and Alembic together for persistence and schema migrations; do not use bare `sqlite3` as an alternative.
- Introduce a client-server database only for concrete requirements (such as high write concurrency, multiple application instances, or database-specific capabilities), after discussing the trade-offs.
- Do not add alternative ORMs, package managers, or infrastructure components without explicit approval.

## Project organization

- Keep application source in `app/` and focused tests in `tests/`. Add `specs/` for significant features and `scripts/` for development, maintenance, or administration utilities only when useful; do not create empty directories or unnecessary layers.
- Use `data/` for persistent user data, including SQLite databases when used; exclude it from version control and never treat it as disposable build output.
- When needed, use `conf/` for local application configuration and exclude it from version control. Keep sanitized, tracked examples or defaults in the sibling `conf.sample/` directory only when needed. Never commit secrets or private environment-specific settings.
- Keep command-line parsing separate from business rules and persistence concerns where that separation adds clarity.
- Prefer existing patterns and small, readable modules over speculative abstractions or generic frameworks.

## CLI behavior

- Use `argparse` for command-line parsing and a clear CLI entrypoint. For installable tools, declare the command in `pyproject.toml` under `[project.scripts]`, pointing to the entrypoint callable in `app/`.
- Provide useful help, clear errors, and meaningful exit codes; keep argument parsing and execution behind the entrypoint so importing modules does not run commands.
- Use emoji only in human-facing status messages. Keep machine-readable outputs (such as JSON, CSV, and piped results) free of emoji and status text; send human-facing status messages to stderr when stdout carries results.

## Quality and testing

- Use Black for formatting, Pylint for linting, pyright in strict mode for static type checking, and pre-commit for relevant automated checks. Existing projects retain their chosen type checker; do not mandate a migration.
- Configure Black and Pylint for a 120-character line length.
- Disable Pylint's `missing-module-docstring`, `missing-class-docstring`, and `missing-function-docstring` checks; do not add boilerplate docstrings just to satisfy linting.
- Use pytest for tests. Add focused tests for meaningful behavior and bug regressions, including argument handling, exit codes, and output contracts where relevant; avoid retroactive coverage of unrelated code.
- Keep development checks reproducible through uv (for example, `uv run pytest`, `uv run pyright`, `uv run black --check app tests`, and `uv run pylint app tests`), adapting commands and tool configuration to the project's actual layout.
- Run applicable checks and report which were performed and which were not.

## Database and persistent data

The following requirements apply only when the tool uses a database.

- Manage schema changes explicitly with Alembic; do not rely on implicit destructive schema recreation.
- If the latest official release and its applicable Alembic revision are not explicitly identified in the project, STOP and ask the user to confirm the release boundary before making any changes to SQLAlchemy data models, database schemas, or Alembic migrations.
- Preserve all migrations up to and including that official revision. Treat subsequent unreleased migrations and the development data associated with them as disposable: consolidate or rewrite migrations into a clean sequence when changing the schema. Never alter the official migration history or discard data belonging to an official release.
- Development databases and data beyond the official release boundary may be reset or discarded when needed to rebuild the consolidated schema. Preserve data from official releases.
- Make schema migrations reviewable and consider compatibility and recovery before applying them.

## Specifications and documentation

- Maintain concise Markdown specifications in `specs/` when useful for significant features, command contracts, and business rules.
- Update relevant specifications when behavior changes; document observable behavior and constraints rather than narrating implementation details.
- Keep `README.md` focused on setup, installation, usage, development, and configuration, not agent session history.
- Record project-specific invariants in the project's own `AGENTS.md`.

## Workflow

- Make the smallest change that fully addresses the task; avoid unrelated refactoring, dependencies, and complexity.
- Follow the project's actual execution and distribution environment rather than assuming a deployment platform or packaging workflow.
- Summarize changes, checks performed, limitations, and decisions left open.
