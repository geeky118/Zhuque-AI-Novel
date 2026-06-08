# 朱雀AI小说 UI Design and Style Guide

## Design Thesis

朱雀AI小说 is a PC-first long-form writing workspace. The interface should feel like a quiet editorial command desk: warm paper surfaces, ink-dark content, restrained cinnabar actions, and muted teal structure. It must support dense repeated use without looking like a template dashboard.

## Brand Tokens

### Color

Use these semantic colors before adding one-off values.

| Token | Value | Usage |
| --- | --- | --- |
| `zq.color.ink` | `#221D18` | Primary text, table header text, high-emphasis icons |
| `zq.color.inkMuted` | `#5D544C` | Secondary copy, descriptions |
| `zq.color.paper` | `#F7F0E6` | Page background and large workspace wash |
| `zq.color.paperSoft` | `#FFFDF8` | Cards, tables, panels, forms |
| `zq.color.cinnabar` | `#A9342B` | Primary buttons, selected states, destructive visual attention only when needed |
| `zq.color.cinnabarDeep` | `#6F1D1B` | Brand mark, strong header accents |
| `zq.color.teal` | `#244E4B` | Sidebar/header structural surfaces |
| `zq.color.tealLight` | `#3D716D` | Header gradient support |
| `zq.color.gold` | `#D8A441` | Small dividers, highlights, subtle icon glints |
| `zq.color.line` | `rgba(111, 29, 27, 0.10)` | Panel and table borders |

Avoid purple gradients, beige-only screens, dark-blue dominance, decorative orb backgrounds, and more than one strong accent color per viewport.

### Typography

- Primary font stack: `"PingFang SC", "Microsoft YaHei", "Heiti SC", Inter, system-ui, sans-serif`.
- Display/title weight: 700-800.
- Operational labels: 12-13px, weight 500-600.
- Body text: 14px with `1.6` line height.
- Dense table text: 13-14px.
- Letter spacing: `0`; do not use negative letter spacing.
- Use serif only for novel-cover title treatment, not for routine admin surfaces.

### Radius and Shadow

- App shell panels: `12-16px`.
- Buttons/inputs: `8-12px`.
- Compact KPI chips: `12-14px`.
- Cards: no more than `12px` unless an existing component needs a larger inherited radius.
- Shadow should be soft and low: prefer `0 18px 42px rgba(34,29,24,0.08-0.12)`.
- Do not nest cards inside cards. Use bordered sections, dividers, or plain layout instead.

## Layout Rules

### PC Shell

- Desktop sidebar width: `220px`, collapsed: `60px`.
- Header height: `70-72px`.
- Page content padding: `24px`.
- Workspace backgrounds may use a subtle `52px` grid plus paper wash.
- Main content max widths are allowed only for reading/editor surfaces. Operational tables should use available width.

### Login

- Split layout: left brand/visual panel, right form panel.
- Brand must read `朱雀AI小说`.
- The right form should be centered, max width about `520px`.
- Do not show default account/password tips, debugging notes, implementation instructions, or setup explanations.

### Bookshelf

- Left shell and top header stay fixed.
- Hero/workspace band is functional: title, brand context, import/export actions.
- Project cards remain the primary interaction.
- Empty/create tile must be visually calm and action-focused.
- No explanatory placeholder copy like "从这里开始" or template/source labels.

### Project Detail

- Sidebar navigation stays stable across child pages.
- Header title and KPI chips must fit at 1920px and 1366px widths without overlap.
- Child pages sit inside a single workspace panel.
- Follow-up/status strips should be compact and only visible when actionable or running.

## Component Treatment

### Buttons

- Primary action: cinnabar gradient or solid cinnabar.
- Secondary action: paper surface with line border.
- Icon buttons should use Ant Design icons; do not replace familiar actions with text-only pills.
- Hover movement must be subtle, max `translateY(-2px)` for routine controls and `-6px` for large project cards.

### Forms

- Inputs: 46px height on login, 36-40px in dense settings pages.
- Prefix icons use muted ink.
- Error states use Ant Design semantic error, not custom red unless the component already uses it.

### Tables and Lists

- Table/card headers should use paper-soft background and ink text.
- Avoid thick borders around every cell; prefer one outer border plus row separators.
- Empty states should use generated or vector-friendly static illustrations only when the page is important enough to need visual orientation.

### Modals and Drawers

- Keep existing behavior.
- Use paper-soft surface, `12-16px` radius, clear title hierarchy.
- Buttons follow the primary/secondary rules.

## Static Asset Rules

Allowed generated assets:

- Phoenix/book logo mark variant.
- Login visual panel texture or illustration.
- Empty-state illustrations for bookshelf, table-heavy pages, and comic generation.
- Placeholder novel-cover presets only for demo or local fallback states.

Asset constraints:

- Generated assets must be saved under `frontend/src/assets/` or `frontend/public/` before code references them.
- Do not leave referenced assets only under `.codex/generated_images`.
- No watermarks, embedded random text, browser frames, or fake UI screenshots inside assets.
- Preserve `/novel` subpath compatibility by referencing assets through existing Vite/public conventions.

## Page-State Coverage

Every routed page must have a PC state entry before final implementation is considered complete.

Generated PC state reference sheets:

| Sheet | File | Coverage |
| --- | --- | --- |
| Auth states | `docs/design/generated/01-auth-states.png` | Login, register, callback, unavailable auth states |
| Bookshelf and creation | `docs/design/generated/02-bookshelf-creation-states.png` | Bookshelf, import, export, cover actions, wizard |
| Global tools | `docs/design/generated/03-global-tools-states.png` | API settings, system settings, MCP, prompts, users |
| Project management A | `docs/design/generated/04-project-management-states-a.png` | World, outline, characters, relationships, graph, organizations/careers |
| Project production B | `docs/design/generated/05-project-production-states-b.png` | Chapters, reader, analysis, foreshadows, style, prompt, comic admin |

Generated images are visual references. If generated small text conflicts with the exact UI labels in this document or source code, source code and this guide are authoritative.

### Authentication

- Login checking
- Local login
- Email login
- Email register
- Auth callback loading
- Auth callback failed

### Home and Project Creation

- Bookshelf empty
- Bookshelf with project grid
- Import modal
- Export modal
- Project wizard start, generation/progress, generated result
- Inspiration page idle, generating, result

### Global Tools

- API settings
- System settings
- MCP plugin list/detail/test
- Prompt template list/create/edit
- User management

### Project Workspace

- World setting
- Careers
- Outline
- Characters
- Relationships
- Relationship graph
- Organizations
- Chapters
- Chapter reader
- Chapter analysis
- Foreshadows
- Writing styles
- Comic style
- Prompt workshop
- Comic admin list/detail/generation

## Fidelity Checklist

For each page group:

- Generated UI reference exists.
- Current route is reachable or has a documented runtime blocker.
- PC screenshot at `1920x1080` exists when runtime is available.
- No visible `MuMuAINovel` in user-facing UI.
- No debug/prototype/implementation copy in formal screens.
- Text does not overlap at 1920px or 1366px.
- Main actions use cinnabar; shell uses teal; content uses paper/ink.
- Existing data fetching, mutations, routing, auth behavior, and SSE behavior are unchanged.
- Targeted lint passes for changed files.
- Build passes.

## Implementation Boundary

Allowed changes:

- CSS-in-JS style objects.
- Ant Design component visual props.
- Class names and shared style constants.
- Static visual assets and references.
- User-facing brand copy required by the rename.

Disallowed changes during style implementation:

- API endpoint names, payloads, response shapes.
- Store actions and lifecycle semantics.
- Permission checks and auth redirects.
- Generation prompts/business algorithms.
- Database schema and migrations.
- Docker compose routing or deployment topology unless deployment verification requires documentation only.
