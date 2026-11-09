# Design System — AI Agent Command Center

**Source:** UI UX Pro Max skill, repository `nextlevelbuilder/ui-ux-pro-max-skill` at commit `4f2dbb6` (2026-11-08).

**How it was produced:** the official install (`npm install -g ui-ux-pro-max-cli` + `uipro init --ai claude`)
was blocked by the development environment's safety policy (twice, including after owner approval).
This file was therefore assembled by **reading the skill's own data files directly** (no skill code executed):
`data/products.csv`, `data/colors.csv`, `data/styles.csv`, `data/typography.csv`, `data/google-fonts.csv`,
and `templates/base/quick-reference.md`. Once the skill can be installed (owner's machine), regenerate with:
`python3 .claude/skills/ui-ux-pro-max/scripts/search.py "AI agent orchestration SaaS dashboard" --design-system --persist -p "AI Agent Command Center"`
and reconcile differences.

## Product match

| Skill row | Used for |
|---|---|
| products #18 *AI/Chatbot Platform* — style "AI-Native UI + Minimalism & Swiss", dashboard "AI/ML Analytics Dashboard" | Overall direction, palette |
| products #7 *Analytics Dashboard* — "Data-Dense Dashboard", "clarity > aesthetics, color-coded data priority" | Dashboard, tables, KPI cards |
| styles #1 *Minimalism & Swiss Style* — best for enterprise apps, dashboards, SaaS; RTL supported | Layout, spacing, type hierarchy |
| styles #28 *Data-Dense Dashboard* — success #22C55E, warning #F59E0B, alert #EF4444 | Status colors |

## Color tokens (colors.csv #18, AI/Chatbot Platform; dark set derived from #6/#24 dark rows)

| Token | Light | Dark |
|---|---|---|
| `--background` | #FAF5FF → neutralized **#F8FAFC** (less tint for data density) | #0B1020 |
| `--foreground` | #1E1B4B | #F1F5F9 |
| `--card` | #FFFFFF | #121831 |
| `--card-foreground` | #1E1B4B | #F1F5F9 |
| `--primary` | #7C3AED | #A78BFA |
| `--primary-foreground` | #FFFFFF | #0F172A |
| `--secondary` | #ECEEF9 | #1E2540 |
| `--secondary-foreground` | #1E1B4B | #E2E8F0 |
| `--accent` (interactions) | #0891B2 | #22D3EE |
| `--muted` | #ECEEF9 | #1A2036 |
| `--muted-foreground` | #475569 | #94A3B8 |
| `--border` | #DDD6FE → **#E2E8F0** | #2A3352 |
| `--ring` | #7C3AED | #A78BFA |
| `--destructive` | #DC2626 | #F87171 |
| `--success` | #16A34A | #22C55E |
| `--warning` | #D97706 | #F59E0B |

Status → color is **never the only signal**: every status badge has an icon and a text label (`color-not-only`).

## Typography (typography.csv / google-fonts.csv)

- **UI (Arabic + Latin):** IBM Plex Sans Arabic — listed for Arabic/RTL + Latin, professional, multi-weight. One family for both languages keeps metrics consistent when switching.
- **Code, IDs, logs:** JetBrains Mono (pairing #9 *Developer Mono*).
- Base 16px, line-height 1.5 (Arabic body 1.7 to avoid clipping diacritics), min body 14px for dense tables.

## Layout

- App shell: collapsible sidebar (start side — right in Arabic, left in English) + top bar with language switcher, theme toggle, user menu.
- Use logical properties only (`ms-*`, `me-*`, `ps-*`, `pe-*`, `start-*`, `end-*`) — never `ml/mr/left/right` — so RTL works without overrides.
- Directional icons (chevrons, arrows) flip in RTL via `rtl:-scale-x-100`.
- Breakpoints verified at 375 / 768 / 1024 / 1440px. No horizontal page scroll; wide tables scroll inside their container.

## Pre-delivery checklist (quick-reference.md, priorities 1–10)

1. Contrast ≥ 4.5:1 text, 3:1 large text/UI; visible focus ring 2px `--ring`; skip link; aria-labels on icon-only buttons.
2. Targets ≥ 24×24 CSS px (44×44 on touch layouts); loading feedback on every async action.
3. Reserve space for async content (skeletons) — no layout shift.
4. Lucide SVG icons only — no emoji as icons.
5. Mobile-first, viewport meta, no horizontal scroll.
6. Semantic tokens only — no raw hex in components.
7. 150–250ms transitions; respect `prefers-reduced-motion`.
8. Visible labels, inline field errors, helper text.
9. Predictable navigation, deep-linkable pages.
10. Charts: legend, tooltip, accessible colors.
