# Changelog

All notable changes to this project are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com), and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- New participation dimensions: `subscribed` (Token holder) and
  `my_reaction_emoji` (default `thumbsup`, override via `?reaction=...`).
  Items found by these dimensions appear in the existing views and
  the new stat row.
- Multi-assignee / multi-reviewer support: `assignee_username` /
  `reviewer_username` only hit the primary assignee/reviewer; the
  helper now also pulls by `assignee_id` / `reviewer_id` (resolved
  via `GET /users?username=X`) so an item where the user is one of
  many assignees/reviewers is correctly included.
- Per-dimension stat row on the board summary view: 5 issues + 6 mrs
  pills, each showing the OR-accumulated count for that dimension
  (assignee / mention / author / reviewer / subscribed / reaction).
  The numbers come from the new `summary.by_relation_counts` field
  (also surfaced via `/api/items`).
- New `query_emoji` constant in `gitlab_issues_finder.queries` for the
  default reaction emoji.

### Changed
- UI reworked to a Codex-inspired visual language: blue accent (was
  orange), larger rounded corners (10–18px vs 4–10px), calmer light
  theme, near-black dark theme. Light / dark / auto theme switching
  retained.
- `_compute_summary` / `_empty_summary` now return a
  `by_relation_counts` field in addition to the existing `by_relation`
  (which is the mutually-exclusive bucket count for the Kanban view).
- `tests/conftest.py` autouse fixture stubs the new fetchers to `[]`
  in tests that do not explicitly opt in, so pre-existing tests do
  not have to register new GitLab API responses.

### Note
- `subscribed` and `my_reaction_emoji` are scoped to the **Token
  holder**, not the queried `username`. To inspect someone else's
  subscriptions / reactions, use their Personal Access Token.

## [Unreleased] - previous

### Added
- FastAPI lifespan context manager replaces the deprecated
  @app.on_event("startup") hook.
- .gitattributes with text=auto and per-extension eol rules so Windows
  contributors do not pollute diffs with CRLF noise.
- Structured logging via gitlab_issues_finder.logging_setup.
  - Human format by default, JSON via LOG_JSON=1.
  - Level controlled by LOG_LEVEL (default INFO).
  - Lifecycle ("startup ok", "startup config incomplete", "shutdown")
    and per-request ("search requested", "search result") events.
- GET /api/version (app + python + fastapi versions).
- GET /api/health (db + config checks; "ok" / "degraded" status).
- Dockerfile (multi-stage: builder + python:3.12-slim runtime,
  non-root user, healthcheck).
- docker-compose.yml + .dockerignore + .env.docker.example.
- GET /api/export.csv and GET /api/export.md: one-shot export of
  the username's items in CSV / Markdown table form.
- In-page card search + sort toolbar on the board view (all /
  issues / mrs / relation / project). 80ms debounce on filter.
- .github/workflows/ci.yml: GitHub Actions running pytest on
  Python 3.10 / 3.11 / 3.12 / 3.13, plus ruff check + format and
  mypy.

### Changed
- queries.py: replaced 7 thin wrapper functions with a single
  fetch_items() factory driven by ItemKind + Relation enums.
  Backward-compat wrappers remain for now; will be removed in a
  future iteration once external callers migrate.
- _db_path() now reads DB_PATH directly from os.environ instead
  of going through AppConfig.from_env(). Decouples local board
  state from GitLab config (regression: previously the function
  raised ConfigError and fell back to "data/app.db" when
  GITLAB_URL/TOKEN were missing, accidentally writing test data
  to the real DB).
- Lint: ruff (replaces ad-hoc discipline). config in pyproject.toml.

## [0.1.0] - 2026-07-03

### Added
- Initial personal tool: open a GitLab username and see all issues
  + merge requests across 4 relations (assignee / mention / author /
  reviewer) plus optional label filter.
- 5-column Kanban with drag-to-override, custom columns, theme
  switcher (auto/light/dark), recent users chips on home.
- SQLite persistence for board overrides, columns, theme, recent
  users.
- 91 unit tests covering config, queries, client exception
  mapping, models, storage, app routes, and board API.
