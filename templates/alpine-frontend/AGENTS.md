# Frontend Project Guidelines

## Purpose

Build maintainable, lightweight frontend applications using the established personal stack. Favor simple, predictable code and deployments over novelty or unnecessary abstractions.

## Required stack

- Eleventy for static site generation and HTML templates.
- Alpine.js for client-side interactivity.
- Bootstrap for UI styling and components.
- TypeScript with strict type checking for application logic.
- esbuild for JavaScript bundling.
- npm for dependency management, with a committed `package-lock.json`.
- Use the latest stable mutually compatible versions when initializing a new project. Do not copy historical version numbers from reference projects.
- Do not replace or add competing frameworks (such as React, Vue, or alternative bundlers) without explicit approval.

## Project organization

- Keep application source under `src/`, tests under `tests/`, and functional specifications under `specs/`.
- Separate application logic from Eleventy templates and static assets, using a simple structure appropriate to the project.
- The build MUST output all deployable static files (HTML, CSS, JavaScript, assets) into a single `dist/` directory. Deployment must not require files from the source tree or other build directories.
- Prefer the smallest architecture that meets current requirements. Do not create layers, abstractions, or configuration solely for hypothetical future needs.

## Quality and testing

- Use ESLint, Prettier, Stylelint, and HTML Validate as the standard quality tools.
- Configure practical scripts for build, type checking, linting, HTML validation, and tests; use consistent npm commands.
- Choose a suitable maintained testing framework for each new project; explain the choice briefly. No specific framework is mandated.
- Keep focused tests in `tests/` and add or update them alongside meaningful behavior changes.
- Do not add multiple testing frameworks without a concrete reason.
- Run applicable checks and clearly distinguish checks performed from checks not performed.

## Specifications and documentation

- Keep concise, behavior-oriented Markdown specifications in `specs/` for significant features and decisions.
- Update relevant specifications when behavior changes; avoid documentation that merely restates code.
- Keep `README.md` focused on setup, development, build, and deployment instructions rather than agent session history.
- Record project-specific constraints and invariants in the project's own `AGENTS.md` as they emerge.

## Implementation and deployment

- Favor accessible, responsive interfaces and straightforward client-side behavior.
- Keep dependencies intentional and avoid introducing additional build systems or runtime services without justification.
- Use reproducible npm installs in CI (for example, `npm ci`).
- Follow the project's actual deployment target and CI conventions; do not invent infrastructure requirements.
