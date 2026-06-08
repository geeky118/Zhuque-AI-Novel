# Project Task Center

## Goal

Add a project-level task entry beside the top-right word count in the project workspace. The entry should show the number of running background tasks and open a side drawer with task details, progress, elapsed time, and errors.

## Constraints

- Preserve existing background task execution paths.
- Prefer a backend aggregation endpoint over duplicating task-status polling logic across pages.
- Keep the UI as an operational panel, not a prototype or explanatory surface.
- Do not touch unrelated working-tree changes.

## Approach

1. Add a backend project task aggregation endpoint that reads currently known task stores:
   - chapter batch generation
   - comic page regeneration/edit tasks
   - comic batch generation
   - full pipeline batch generation
   - storyboard generation tasks
   - batch outline expansion tasks when tied to the project
   - visual bible batch status
2. Add frontend types and API client method.
3. Add a reusable task drawer component with progress, elapsed time, status, and error details.
4. Mount the task entry in `ProjectDetail` beside the `已写` stat and poll while the project view is open.

## Validation

- Backend import smoke: `cd backend && python -m compileall app`
- Frontend build: `cd frontend && npm run build`

## Evidence

- Added `GET /api/projects/{project_id}/tasks` to aggregate project background task state.
- Added the `ProjectTaskDrawer` entry beside the desktop `已写` stat.
- Backend import smoke passed: `cd backend && python -m compileall app`.
- Frontend production build passed: `cd frontend && npm run build`.

## Remaining Risks

- Some legacy background tasks only exist in process memory and disappear after backend restart; the panel can only show what the current runtime or persisted task files expose.
