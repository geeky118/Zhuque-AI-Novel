# 朱雀AI小说 Page-State Coverage Audit

## Scope

This audit maps the requested PC-first page-state UI generation and implementation coverage to concrete repository artifacts.

The generated UI sheets are design references. Exact production labels, data values, validation text, and permission behavior remain defined by the React source and backend contracts.

## Generated UI Reference Coverage

| Generated sheet | Covered page states |
| --- | --- |
| `docs/design/generated/01-auth-states.png` | login checking, local login, email login, email register, callback loading/failure, unavailable auth states |
| `docs/design/generated/02-bookshelf-creation-states.png` | bookshelf empty, bookshelf grid, import/export modal direction, project wizard idle/progress/result, inspiration idle/progress/result |
| `docs/design/generated/03-global-tools-states.png` | API settings, system settings, MCP plugin list/detail/test, prompt template list/edit, user management list/actions |
| `docs/design/generated/04-project-management-states-a.png` | project detail shell, world setting, careers, outline, characters, relationships, relationship graph, organizations |
| `docs/design/generated/05-project-production-states-b.png` | chapters, chapter reader, chapter analysis, foreshadows, writing styles, comic style, prompt workshop, comic admin |

## Implementation Coverage

| Route or surface | Primary implementation files | Coverage mechanism |
| --- | --- | --- |
| `/login` | `frontend/src/pages/Login.tsx` | Dedicated split auth screen styled with Zhuque tokens and brand copy |
| `/auth/callback` | `frontend/src/pages/AuthCallback.tsx`, `frontend/src/index.css` | Global paper background, Ant Design theme, and auth-state sheet direction |
| `/`, `/projects` | `frontend/src/pages/ProjectList.tsx`, `frontend/src/pages/BookshelfPage.tsx` | Dedicated PC shell, bookshelf cards, hero/action styling |
| book import embedded tool | `frontend/src/pages/BookImport.tsx` | Dedicated Zhuque tool header and paper workspace surface |
| `/wizard` | `frontend/src/pages/ProjectWizardNew.tsx` | Dedicated creation shell aligned with creation-state sheet |
| `/inspiration` | `frontend/src/pages/Inspiration.tsx` | Dedicated generation shell aligned with creation-state sheet |
| `/settings` | `frontend/src/pages/Settings.tsx` | Dedicated global tool header, panel, and provider branding updates |
| `/prompt-templates` | `frontend/src/pages/PromptTemplates.tsx` | Dedicated global tool header, Zhuque action surfaces, purple-copy removal |
| `/mcp-plugins` | `frontend/src/pages/MCPPlugins.tsx` | Dedicated global tool header and primary action restyle |
| `/user-management` | `frontend/src/pages/UserManagement.tsx` | Dedicated PC table shell with Zhuque header and paper table panel |
| system settings embedded tool | `frontend/src/pages/SystemSettings.tsx` | Dedicated global tool header and paper settings cards |
| `/project/:projectId/*` | `frontend/src/pages/ProjectDetail.tsx`, project child pages | Stable project shell, sidebar, header, workspace panel, global Ant Design styling |
| management child pages | `WorldSetting.tsx`, `Careers.tsx`, `Outline.tsx`, `Characters.tsx`, `Relationships.tsx`, `RelationshipGraph.tsx`, `Organizations.tsx` | Inherit project shell plus global cards, tables, tags, tabs, form, and selected-state styling |
| production child pages | `Chapters.tsx`, `ChapterReader.tsx`, `ChapterAnalysis.tsx`, `Foreshadows.tsx`, `WritingStyles.tsx`, `ComicStyle.tsx`, `PromptWorkshop.tsx`, `ComicAdmin.tsx` | Inherit project shell plus global cards, tables, tags, tabs, form, modal, drawer, and progress styling |

## Shared Style Coverage

| Layer | File | Coverage |
| --- | --- | --- |
| Brand tokens | `frontend/src/theme/zhuqueTokens.ts` | brand name, ink/paper/cinnabar/teal/gold palette, font stack |
| Ant Design theme | `frontend/src/theme/themeConfig.ts` | primary color, layout/container surfaces, radius, base typography |
| Global CSS | `frontend/src/index.css` | page background, menu, cards, tables, forms, buttons, tags, tabs, modals, drawers, progress |
| Design spec | `docs/design/zhuque-ui-style-guide.md` | design thesis, tokens, layout rules, component treatment, static asset rules, fidelity checklist |

## Brand Replacement Coverage

Visible application brand now uses `朱雀AI小说`.

`朱雀API` is used for the visible image-provider name while retaining the internal provider value `mumu`, so existing API logic and persisted settings remain compatible.

The GitHub URL in `frontend/src/config/version.ts` still contains `MuMuAINovel` because it is the repository address, not a user-facing brand label.

## Verification Status

Passed:

- `cd frontend && npx.cmd eslint src/pages/Settings.tsx src/pages/PromptTemplates.tsx src/pages/MCPPlugins.tsx src/pages/UserManagement.tsx src/pages/SystemSettings.tsx src/pages/BookImport.tsx src/theme/themeConfig.ts src/theme/zhuqueTokens.ts`
- `cd frontend && NODE_OPTIONS=--max-old-space-size=4096 npm run build`
- `cd backend && python -m compileall app`

Known limits:

- Full `npm run lint` is still blocked by pre-existing hook-rule violations in `Chapters.tsx` and `Characters.tsx`.
- Local runtime screenshot QA is blocked because the Vite dev server proxies `/api/*` to a local `8000` service that times out, and a separate backend process previously hit `MemoryError` during `torch/sentence_transformers` import.
- Test-server deployment is pending SSH credentials.
