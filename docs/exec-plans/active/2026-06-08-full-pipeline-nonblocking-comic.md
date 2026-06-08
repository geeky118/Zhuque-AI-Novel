# Full Pipeline Nonblocking Comic Stage

## Goal

Speed up batch full-pipeline generation by preventing slow comic image generation from blocking the next chapter's text, analysis, and storyboard work.

## Constraints

- Keep chapter text generation sequential so previous chapter summary context remains stable.
- Keep analysis immediately after each generated chapter when enabled.
- Keep storyboard immediately after chapter/analysis.
- Do not change the single-page comic generation worker behavior.
- Avoid multiplying image concurrency by chapter count.

## Approach

The full-pipeline loop now queues comic page generation after each chapter storyboard is ready, starts those page jobs asynchronously under one pipeline-level semaphore, and immediately continues to the next chapter. After all chapters have passed the text/analysis/storyboard path, the pipeline waits for all queued comic page jobs before marking the full-pipeline task completed.

Additional hardening:

- Comic image HTTP calls now have explicit connect/write/pool/overall timeouts.
- Transient image errors get more retry attempts, including Chinese timeout/busy hints.
- Whole-chapter comic regeneration runs queued page jobs through a shared concurrency gate.
- The chapter page batch comic modal exposes comic page concurrency.
- Full-pipeline chapter results now record a final comic page status summary after async page jobs finish.

## Validation

- Backend import smoke: `cd backend && python -m compileall app`
- Frontend build: `cd frontend && npm run build`

## Evidence

- Backend import smoke passed.
- Frontend production build passed.
- Re-ran both checks after timeout/retry/concurrency hardening.

## Remaining Risks

- Pipeline chapter results now include page status summary, while detailed per-page errors remain in comic page task state.
- Cancelling a full-pipeline task while comic page jobs are already running still depends on existing worker behavior.
