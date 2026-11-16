# Frontend Project Guidelines

## Purpose

Build maintainable, lightweight frontend applications using the established personal stack. Favor simple, predictable code and deployments over novelty or unnecessary abstractions.

## Language policy

- Use the user's language for conversation, specifications, and reports.
- Use English by default for code, comments, documentation, logs, and CLI output, unless the user explicitly requests an exception.
- Specify UI language per project. Never infer it from the user's language or source code; ask when it is unspecified.

## Required stack

- Eleventy for static site generation and HTML templates.
- Alpine.js for client-side interactivity.
- Bootstrap for UI styling and components.
- TypeScript with strict type checking for application logic.
- Use the native Bun bundler (`Bun.build()` or `bun build`) for JavaScript/TypeScript bundling; do not add esbuild, webpack, or Vite by default.
- Use Bun as the runtime, package manager, script runner, bundler, type checker, and test runner for new projects; commit `bun.lock`. For existing projects, preserve established tooling unless migration is explicitly requested.
- Use the latest stable mutually compatible versions when initializing a new project. Do not copy historical version numbers from reference projects.
- Do not replace or add competing frameworks (such as React, Vue, or alternative bundlers) without explicit approval.

## Project organization

- Keep application source under `src/` and tests under `tests/`. Use `specs/` for significant functional specifications when useful; do not create placeholder documentation.
- Separate application logic from Eleventy templates and static assets, using a simple structure appropriate to the project.
- The build MUST output all deployable static files (HTML, CSS, JavaScript, assets) into a single `dist/` directory. Deployment must not require files from the source tree or other build directories.
- Prefer the smallest architecture that meets current requirements. Do not create layers, abstractions, or configuration solely for hypothetical future needs.

## Quality and testing

- Prefer Bun-native APIs and tooling wherever they cover the requirement. Avoid redundant runtime, bundler, package-manager, and testing dependencies.
- Use HTML Validate to validate generated HTML.
- Configure `package.json` scripts for build, type checking, formatting, HTML validation, and a consolidated `check` command. Add dedicated linting only when justified.
- Use `bun check --no-script` for type checking, `bun fmt` for formatting, and `bun test` for testing. Do not introduce alternative tools without a concrete requirement.
- Do not require `tsc --noEmit` or the TypeScript package solely for type checking; retain TypeScript when required by editor tooling or declaration generation.
- Keep focused tests in `tests/` and update them alongside meaningful behavior changes.
- Run applicable checks and clearly distinguish checks performed from checks not performed.

## Specifications and documentation

- Keep concise, behavior-oriented Markdown specifications in `specs/` for significant features and decisions.
- Update relevant specifications when behavior changes; avoid documentation that merely restates code.
- Keep `README.md` focused on setup, development, build, and deployment instructions rather than agent session history.
- Record project-specific constraints and invariants in the project's own `AGENTS.md` as they emerge.

## Implementation and deployment

- Favor accessible, responsive interfaces and straightforward client-side behavior.
- Keep dependencies intentional and avoid introducing additional build systems or runtime services without justification.
- For Bun projects, use Bun end-to-end in CI: `bun install --frozen-lockfile` followed by `bun run check`. The consolidated `check` script must cover type checking, formatting, tests, production build, and generated HTML validation. Do not install Node.js or npm solely out of convention.
- Follow the project's actual deployment target and CI conventions; do not invent infrastructure requirements.
