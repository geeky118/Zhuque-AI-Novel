# Verification Runbook

Use this runbook to choose the smallest meaningful validation for a change. Record skipped checks and the reason in the handoff or active execution plan.

## Baseline Checks

Frontend production build:

```bash
cd frontend
npm run build
```

Frontend lint:

```bash
cd frontend
npm run lint
```

Backend import smoke:

```bash
cd backend
python -m compileall app
```

Docker service health:

```bash
docker compose up -d --build
docker compose ps
curl http://localhost:18000/health
```

## Runtime Smoke

When Docker is available:

```bash
docker compose up -d postgres
docker compose up -d --build mumuainovel
curl http://localhost:18000/health
```

Then verify in the browser:

- Login works with the configured local account.
- `/projects` loads.
- A project detail page loads.
- A relevant feature page for the change loads without console/API errors.
- If the change touches routing, repeat under `/novel` or the reverse-proxy subpath used by deployment.

## Feature-Specific Checks

Chapter generation or regeneration:

- Create or select a project with at least one chapter.
- Trigger the changed generation path.
- Confirm task progress reaches completed or failed with a clear message.
- Confirm generated content persists after refresh.

Chapter analysis:

- Trigger single-chapter or batch analysis.
- Confirm `analysis_tasks` moves out of `pending/running`.
- Confirm `plot_analysis` is created or updated.
- Confirm failed provider calls surface as `failed` task status.

Book import:

- Create an import task from a representative `.txt`.
- Review preview chapter boundaries.
- Apply import into a project.
- Confirm created chapters preserve order, title, and content.

Character images and comics:

- Generate or select a character image variant.
- Confirm artifact metadata is stored.
- Trigger combined comic chapter data.
- Confirm chapter-appropriate character image references appear in the payload.

Prompt/template pages:

- Load the page under normal root routing.
- Load the same page under `/novel` when possible.
- Confirm API requests use the shared API client or base-path helper.

Auth and user management:

- Login.
- Refresh the page.
- Confirm protected routes stay accessible.
- Confirm unauthenticated requests redirect to the correct login path.

## Database Checks

Open PostgreSQL in the Docker service:

```bash
docker exec -it mumuainovel-postgres psql -U mumuai -d mumuai_novel
```

Useful task-state query:

```sql
SELECT status, count(*)
FROM analysis_tasks
GROUP BY status
ORDER BY status;
```

Useful chapter analysis query:

```sql
SELECT c.chapter_number, c.title, t.status, t.progress, pa.id IS NOT NULL AS has_analysis
FROM chapters c
LEFT JOIN LATERAL (
  SELECT *
  FROM analysis_tasks t
  WHERE t.chapter_id = c.id
  ORDER BY t.created_at DESC
  LIMIT 1
) t ON true
LEFT JOIN plot_analysis pa ON pa.chapter_id = c.id
WHERE c.project_id = '<project-id>'
ORDER BY c.chapter_number;
```

## Evidence Standards

- A build command proves syntax/bundling for the files it covers, not runtime behavior.
- `python -m compileall app` proves Python import/compile syntax, not database or provider correctness.
- A green HTTP `/health` proves the backend process is alive, not that a feature is correct.
- A browser smoke is required when changing route, path, auth, visual workflow, or user-facing API behavior.
- Database evidence is required when fixing stuck tasks, migrations, or persistence behavior.
