# MuMuAINovel Technical Architecture

## Purpose

MuMuAINovel is a web-based production workspace for AI-assisted long-form fiction. The system is organized around a novel project and supports planning, writing, analysis, continuity tracking, prompt management, book import, visual assets, and comic generation.

## Runtime Shape

```text
Browser
  |
  | React SPA routes, /api/* requests, /generated-assets/*
  v
FastAPI application (backend/app/main.py)
  |
  | SQLAlchemy async sessions
  v
PostgreSQL

External services:
  - OpenAI-compatible / Anthropic / Gemini text APIs
  - image generation providers
  - ChromaDB + sentence-transformers for story memory retrieval
  - Tencent COS for optional generated asset upload
  - MCP plugin tools when enabled
```

In Docker Compose, the `mumuainovel` container serves the built frontend and backend API. The `postgres` container stores application data. Local generated files are mounted from `storage/` and runtime logs from `logs/`.

## Frontend Architecture

Frontend source lives in `frontend/src`.

Key layers:

- `App.tsx`: route declarations and protected route composition.
- `pages/`: feature-level screens such as projects, outline, characters, chapters, analysis, prompt workshop, comics, settings, and user management.
- `components/`: reusable UI and workflow components such as chapter reader, analysis modal, regeneration UI, comic drawer, theme controls, and SSE progress widgets.
- `services/api.ts`: shared Axios API client, API method catalog, response error handling, and auth redirect handling.
- `utils/basePath.ts`: `/novel`-safe path construction for SPA and API routes.
- `utils/sseClient.ts`: shared server-sent event client for streaming generation workflows.
- `theme/`: theme context, Ant Design theme config, and persistence.
- `types/`: TypeScript contracts shared by pages and API services.

Build behavior:

- `frontend/vite.config.ts` builds local production assets into `backend/static`.
- The Dockerfile temporarily changes the Vite output to `dist` during the frontend build stage, then copies the result into the backend image.
- Vite dev server proxies `/api` and `/generated-assets` to the backend.

Routing invariant:

- Use `buildAppPath` and `buildApiPath` for app/API links that can be served from `/novel`.
- Avoid hardcoded root-relative API calls such as `/api/...` in feature code.

## Backend Architecture

Backend source lives in `backend/app`.

Key layers:

- `main.py`: FastAPI app setup, middleware, health checks, router registration, static SPA serving, generated cover asset mount.
- `config.py`: Pydantic settings loaded from `.env`.
- `database.py`: async engine/session management, connection pool settings, session health stats, model registration.
- `middleware/`: request ID and authentication middleware.
- `api/`: HTTP route modules. Each module owns request validation, permission checks, orchestration, and response shaping for one feature area.
- `models/`: SQLAlchemy ORM models.
- `schemas/`: Pydantic request/response schemas.
- `services/`: reusable domain services such as AI orchestration, prompt formatting, memory extraction, import/export, foreshadow sync, image generation, career/state updates, and MCP helpers.
- `mcp/`: MCP client facade and plugin status synchronization.
- `utils/`: shared utility helpers such as SSE responses and consistency checks.

Router map:

- Auth/admin/users/settings: `auth.py`, `admin.py`, `users.py`, `settings.py`
- Core authoring: `projects.py`, `outlines.py`, `characters.py`, `chapters.py`, `writing_styles.py`
- Continuity: `memories.py`, `foreshadows.py`, `relationships.py`, `organizations.py`, `careers.py`
- Generation workflows: `wizard_stream.py`, `inspiration.py`, `polish.py`, `project_covers.py`
- Prompt workflows: `prompt_templates.py`, `prompt_workshop.py`
- Book import: `book_import.py`
- Visual/comic workflows: `character_images.py`, `comics.py`
- Integrations: `mcp_plugins.py`, `changelog.py`

## Data Architecture

Primary relational data is stored in PostgreSQL.

Core entities:

- `users`: local/OAuth/email user records and authorization context.
- `projects`: root novel projects and project-level settings.
- `outlines`: story outline nodes and expansion planning data.
- `chapters`: chapter content, summary, ordering, and outline linkage.
- `analysis_tasks`: async chapter analysis task status.
- `plot_analysis`: structured plot analysis results for chapters.
- `story_memories`: relational memory records derived from analysis.
- `characters`, `relationships`, `organizations`, `careers`: continuity and cast modeling.
- `foreshadows`: planted/resolved clue tracking.
- `prompt_templates`, `prompt_workshop`: local and workshop prompt data.
- `batch_generation_tasks`, `regeneration_tasks`: long-running writing task status.
- `character_image_artifacts`, `comic_storyboard_artifacts`, `comic_page_artifacts`: generated visual artifact records.

Migrations:

- PostgreSQL migrations live under `backend/alembic/postgres/`.
- SQLite migrations are present for legacy/local compatibility under `backend/alembic/sqlite/`.
- The Docker image uses the PostgreSQL Alembic config and runs migrations from `backend/scripts/entrypoint.sh`.

Memory retrieval:

- `memory_service.py` stores and retrieves story memory embeddings.
- ChromaDB files are stored under `data/chroma_db` or the configured container mount/cache.
- Sentence-transformers model files are cached under the configured embedding directory.

## AI And Generation Flows

Text generation:

1. API route receives a generation request and verifies project/user access.
2. Route builds context through domain services.
3. `AIService` selects provider/client based on user/system settings.
4. Generation runs either as direct text response or SSE stream.
5. Results are persisted and task status is updated.

Chapter analysis:

1. `chapters.py` creates an `analysis_tasks` row.
2. Background analysis loads chapter text, existing foreshadows, and relevant character context.
3. `plot_analyzer.py` prompts the configured text model and parses structured JSON.
4. The backend writes `plot_analysis`, `story_memories`, character state/career updates, relationship changes, and foreshadow state changes.
5. Frontend polls task status through batch-safe endpoints.

Book import:

1. `book_import.py` creates a parsing/import task.
2. `txt_parser_service.py` detects chapter boundaries and produces a preview.
3. The apply step persists chapters and optional project metadata.

Comic generation:

1. `comics.py` combines chapter, storyboard, style, and character image data.
2. Storyboard/page generation writes local state under `/tmp/mumuainovel_comic_state` and database artifact records.
3. Generated pages can be uploaded to COS when configured.

Character images:

1. `character_images.py` generates or edits variants for a character.
2. Period/chapter metadata is stored with image artifacts.
3. Comic workflows select chapter-appropriate references when available.

## Authentication And Authorization

- `AuthMiddleware` attaches user context to requests.
- Protected frontend routes use `ProtectedRoute`.
- Local account auth is controlled by `LOCAL_AUTH_*`.
- OAuth/email modes are controlled by `LINUXDO_*`, `EMAIL_*`, and `SMTP_*`.
- Most API routes call shared access checks such as `verify_project_access`.

## Static Assets And Generated Files

- Built SPA assets: `backend/static/`.
- Generated cover and image assets: Tencent COS URLs recorded in the database; local files are not the durable source.
- Character image and comic state paths are mounted in Docker Compose:
  - `/tmp/mumuainovel_character_images`
  - `/tmp/mumuainovel_comic_state`
- Logs are mounted at `logs/`.

## Deployment Architecture

Docker Compose services:

- `postgres`: PostgreSQL 18 Alpine, persistent named volume `postgres_data`.
- `mumuainovel`: FastAPI app image built from the repo, depends on PostgreSQL health, serves API and SPA.

Build stages:

1. Node builder installs frontend dependencies with pnpm and builds the SPA.
2. Python runtime installs backend dependencies and copies built static assets.
3. Entrypoint runs migrations and starts the app.

Reverse proxy note:

- The app may be mounted under `/novel`.
- Frontend code must use base-path helpers for app and API paths.
- Reverse proxy configuration should preserve cookies and forward `/novel/api/*` to backend `/api/*` according to the chosen mount strategy.

## Operational Invariants

- Database writes in long-running chapter/batch workflows should update task rows even on failure.
- Pending/running task states must have timeout recovery paths so the UI cannot stay stuck forever.
- Background workers must not share request-scoped DB sessions.
- User-facing routes and API calls must be subpath-safe.
- Generated assets must have deterministic storage paths or database records sufficient for cleanup and audit.
- New schema fields require PostgreSQL Alembic migrations.
- External provider errors should be surfaced as actionable task failure messages, not silent retries.

## Extension Points

- Add a new page: create `frontend/src/pages/<Feature>.tsx`, add route in `App.tsx`, and expose API calls in `services/api.ts`.
- Add a backend feature: create model/schema/service/api modules, register the router in `main.py`, and add migrations if persistence changes.
- Add an AI provider: implement a provider/client under `backend/app/services/ai_providers` or `ai_clients`, wire settings, and update `AIService` selection.
- Add a long-running workflow: persist a task model, expose status endpoints, implement timeout recovery, and document validation in `docs/runbooks/verification.md`.
