# AMS System (FBM)

Flask web app for managing accounts, payments, direct sales, deliveries, and inventory.

## Stack
- Python 3.11
- Flask 3.x with Flask-SQLAlchemy and Flask-Login
- SQLite (file at `instance/ahmed_cement.db`)
- Bootstrap-based templates in `templates/`

## Entry point
- `app.py` is the run entry (delegates to `main.py` via `create_app()`).
- `main.py` holds the bulk of routes; blueprints live in `blueprints/` (accounts, admin, inventory, import_export, data_lab).

## Workflow
- "Start application" runs `python app.py` on port 5000 (webview).
- Default login: `admin` / `admin12345`.

## Recent changes
- 2026-05-05: **Receipt overhaul — all 5 receipt templates redesigned for A5 paper**
  - Templates changed: `view_bill.html`, `payment_receipt.html`, `client_ledger_print.html`, `supplier_ledger_print.html`, `supplier_payment_receipt.html`
  - All receipts now use a shared professional layout: `rcpt-header` (≤2 inches, `position:fixed` top in print), `rcpt-body` (transaction data, with matching top/bottom margins), `rcpt-footer` (≤2 inches, `position:fixed` bottom in print).
  - `@page { size: A5 portrait; margin: 0 }` on all receipts; body gets `margin-top:50mm; margin-bottom:48mm` so content never overlaps the fixed header/footer.
  - Company name, address, and phone now pulled from the `settings` context variable (available globally), eliminating inconsistent hard-coded values and duplicate phone number entries across templates.
  - Bill/MB number shown exactly once per receipt (removed the duplicate badge display in `view_bill.html`).
  - Professional typography: company name 22px bold, bordered doc-type badge, clean section titles, consistent table styling with `#f0f0f0` header rows.
  - Screen view: receipts appear as a clean 600px-wide white card with normal document flow; `d-print-none` hides toolbar controls when printing.

## Recent changes
- 2026-04-28: **Direct Sales template rebuilt from scratch** (`templates/direct_sales.html`, 2 848 ln). Add Sale and Edit Sale modals are now `modal-fullscreen` (no longer side-sheets). Item-row grid switched to `align-items: stretch` with each cell as a flex column, so when one cell grows (helper text, status badge), every other cell in that row stretches to the same height — fixing the misaligned-row regression. The entire JS block (1 595 lines, including `attachSaleListeners`, `updateSalePrice`, `checkItemBookingStatus`, `updateBookingStatus`, all `addSaleItemRow*` / `add/removeDeliveryPersonRow` / draft / validation logic) was preserved verbatim. Every backend hook (`name=`, `id=`, `action=`, hidden inputs, `{% if %}` branches, `data-booking-container`, `data-financial-container`, `.compact-item-row`, `.sale-item-grid`, `.delivery-row`, `.ignore-booking-item`) carried across unchanged. `templates/direct_sales.html.legacy` kept for one-commit rollback until Phase 12. Live-verified: `GET /direct_sales` returned 200 in 172 ms; subsequent `/api/client_booking_status/...` and `/api/client_financial_summary/...` calls confirm the new fullscreen modal opens and JS hooks fire.
- Direct Sales (`templates/direct_sales.html`): Cash Sale now always shows the Payment Account selector, accepts both cash and bank accounts, and auto-syncs the payment method when a bank account is picked. Edit form refreshes the selector when the sale type changes.
- Sidebar (`templates/layout.html`): "FBM Cash Drawer" link removed. The route `/fbm_cash_drawer` still exists in `main.py` for backward compatibility.
- 2026-04-28: UI audit + safe migration plan added at project root.
  - `UI_AUDIT_REPORT.md` — page-by-page inventory of all 80 templates, what each screen does, and what's wrong with its UI today (objective metrics: inline-style counts, custom CSS line counts, etc.).
  - `UI_MIGRATION_PLAN.md` — 13-phase, zero-loss migration. Backend is not touched. Each phase converts one screen / one section, behind a shared component layer, with a per-page validation checklist and a one-commit rollback.

### UI v2 progress
- **Phase 0 (Foundations)** done.
  - New `static/ui.css` (~430 lines): design tokens (`--ui-*`), KPI tile, page header, card, form grid, data table, side-sheet drawer, theme-aware combobox skin. Theme-reactive in dark and light.
  - New `static/ui.js`: side-sheet open/close (`AMSUI.openSheet`), unified flatpickr init for `.ui-date` / `.ui-datetime` inputs, `tr[data-href]` row drill-down binder, combobox skin pass.
  - Both files now loaded globally from `templates/layout.html` after `theme.css` / `theme.js`.
  - Shared partials in `templates/_ui/`: `page_header.html`, `kpi_tile.html`, `data_table_card.html`, `filter_bar.html`.
  - Dead `templates/base.html` (Bootstrap 4 legacy, zero references) removed.
- **Phase 1 (Shell polish)** done — `ui.css` + `ui.js` wired into `layout.html`. Sidebar, nav links, modals, theme switcher untouched (only the new shared layer was added).
- **Phase 2 (Dashboard, every card drillable)** done — `templates/index.html` rewritten on the new component layer:
  - 6 KPI tiles, **all clickable**, each drills to its detail page:
    Total Inventory → `/inventory/stock_summary`, Registered Clients → `/clients`,
    Daily Cash → `/financial_details?type=cash`, Daily Due → `/financial_details?type=credit`,
    Total Outstanding → `/unpaid_transactions?status=unpaid`,
    Accounts Hub → `/accounts/` (admin / payments-manager only).
  - Quick Actions panel kept (now uses `.ui-quick-grid`).
  - Brand Stock table — every row drills to that brand's daily breakdown via `tr[data-href]` (`/inventory/daily_transactions?material=<name>`).
  - All inline hex / `onmouseover` / inline gradients removed from `index.html`.
- Regression tested via Flask test_client: `/`, `/accounts/`, `/clients`, `/payments`, `/bookings`, `/direct_sales`, `/inventory/stock_summary`, `/financial_details?type=cash`, `/unpaid_transactions?status=unpaid` all return 200 with no template errors.
- **Phase 3 (Direct Sales — page chrome)** done — `templates/direct_sales.html` (3,133 lines) migrated surgically. The huge Add Sale / Edit Sale / Hold Drafts modals were left bit-identical (every `<input>`, `<select>`, hidden field, `name=`, `id=`, `action=`, every `{% if %}` branch and every JS handler is preserved). What changed:
  - Page header now uses `_ui/page_header.html` (icon + title + subtitle + right-side toolbar). Back / Mixed Report / Add Sale buttons unchanged.
  - Filter card: stripped `bg-dark border-secondary` + per-input `bg-dark text-white border-secondary` overrides; now uses `.ui-card` + `.ui-label` so it inherits theme tokens (works in both dark and light).
  - The two "Billed Sales" / "Unbilled Sales" add-cards were inline-styled `<div>`s with `onmouseover="this.style.transform=…"`. Replaced with semantic `<button>`s using new `.ds-add-tile` class — real CSS hover, focus-visible outline, keyboard-clickable.
  - Sales table card: removed inline `style="background:#1e293b; border:2px solid #475569 !important; border-radius:15px"` and the `<thead style="background:#0f172a">` / `<tbody style="background:#1e293b">` / `<tr style="border-bottom: 1px solid #334155">` inline hex. Now uses `.ui-card .ds-table-card` + `.ds-sales-table` with `.ds-row-void` modifier for voided rows. All 11 columns (Bill No / Status / Client / Date / Time / Product / Qty / Total / Paid / Driver Rent / Actions) preserved in same order.
  - **Date picker swap (plan §2.3 + §9.4):** dropped the `air-datepicker@3.6.0` CDN `<link>` and `<script>`. `initSaleDatePickers()` now uses `flatpickr` (already loaded by `layout.html`) with `enableTime, dateFormat:'Y-m-d H:i', time_24hr, allowInput`. Calendar button on `.sale-date-shell` still opens the picker via `instance.open()`. Native fallback (`fallbackNativeDateTimeInputs`) untouched. The dead `.air-datepicker {}` CSS rule and `.air-date-time` marker class are left in place until Phase 12 cleanup (harmless — library no longer loaded).
  - 533 lines of inline `<style>` (lines 4-441) intentionally kept — they style the modal interiors (sale-form grid, compact-item-row, sale-date-shell, etc.) and will be migrated together with the modals in a follow-up Phase 3.B sitting (side-sheet refactor).
  - Added Phase 3 component classes to `static/ui.css` (~120 lines): `.ui-label` (top-level), `.ds-mode-tabs`, `.ds-add-grid`, `.ds-add-tile` + variants, `.ds-add-cta` + variants, `.ds-table-card`, `.ds-sales-table` (theme-reactive thead / tbody / hover), `.ds-row-void`, `.ds-filter-card`. All theme-aware via `--ui-*` tokens.
  - Old template preserved at `templates/direct_sales.html.legacy` for one-commit rollback (per plan §5).
  - Per-page validation: `/direct_sales`, `/direct_sales?show=voided`, `/direct_sales?show=all`, `/direct_sales?bill_state=billed&page=1` all return 200. Rendered HTML contains `addSaleModal`, `editSaleModal`, `holdDraftsModal`, `voidSaleForm`, `unvoidSaleForm` (on voided page), `Restore` button (on voided page), `name="show"` hidden input, `flatpickr(` JS init, and **zero** `AirDatepicker` references.
- **Phase 3.B (Direct Sales — Add Sale / Edit Sale modals → right-side sheet drawer)** done:
  - New CSS in `static/ui.css` (~50 lines): `.modal-dialog.ui-sheet-modal` makes any Bootstrap modal slide in from the right as a fixed-width side panel (`min(960px, 96vw)` on desktop) and full-screen drawer on mobile (`<= 767.98px`). `.modal-content.ds-sale-sheet` reads `--ui-surface` / `--ui-surface-2` / `--ui-border` so the sale form's surface, header, and footer follow the active theme instead of a hard-coded `#1e293b` / `#111827`.
  - **All Bootstrap modal mechanics preserved** — `data-bs-toggle="modal"` / `data-bs-target="#addSaleModal"` triggers, `data-bs-dismiss="modal"` close buttons, and `bootstrap.Modal.getOrCreateInstance(...)` JS calls all keep working. Only the visual presentation changes; the JS layer is untouched.
  - In `templates/direct_sales.html`:
    - `addSaleModal`: `<div class="modal-dialog modal-fullscreen">` → `<div class="modal-dialog ui-sheet-modal">`. The `<form>` lost its inline `style="background: #1e293b;"` and `border-secondary` class; gained `ds-sale-sheet`. Everything inside the form (every `<select>`, `<input>`, `<button>`, hidden field, `name=`, `id=`, every JS hook including `submitHoldSale()`, `openHoldDraftsModal()`, `resetAddSaleForm()`, `addSaleItemRow()`, `addDeliveryPersonRow(this)`, `removeDeliveryPersonRow(this)`, `changeSaleQty(this, ±1)`, `selectSaleMaterial(...)`, `filterAddSaleClientsByCategory()`, `updateBookingStatus(...)`, `updateClientFinancialSummary(...)`) is bit-identical to before.
    - `editSaleModal{{ sale.id }}` (in the `{% for sale in sales %}` loop): same swap. Same preservation guarantee for `editSaleClientCode{{id}}`, `editSaleCategory{{id}}`, `editSaleMaterialSearch{{id}}_{{i}}`, `editSaleAltMaterialSearch{{id}}_{{i}}`, `editSaleManualBill{{id}}`, `editHasBillCheck{{id}}`, `editSaleManualClientName{{id}}`, the `data-booking-container` / `data-financial-container` form attributes, and the `Save Changes` submit.
    - `holdDraftsModal` left as-is (small read-only centered modal — doesn't need a side-sheet).
    - The inline `<style>` block at the top of `direct_sales.html` had its `#addSaleModal .modal-dialog` / `.modal-content` overrides removed (those forced `max-width: 100vw; margin: 0; min-height: 100vh; border-radius: 0; border: 0` to make the fullscreen modal). The sticky header/footer rules were kept (so the toolbar stays pinned while the body scrolls) but their hard-coded `background: #111827` and `border: 1px solid #334155` were removed — the new `.ds-sale-sheet .modal-header / .modal-footer` rules in `ui.css` now provide token-driven backgrounds and borders.
  - The remaining inline `<style>` in `direct_sales.html` (the form-internals: `.sale-form`, `.compact-item-row`, `.sale-date-shell`, `.delivery-row`, `.item-total-box`, `.qty-rate-row`, the `--row-*` grid columns, the responsive `@media` breakpoints) is intentionally untouched — it styles the inside of the modals, which already has full theme-light coverage in `static/theme.css` (lines 414-458) via `html[data-theme="light"] .sale-form ...` rules. Migrating it into `ui.css` would be churn for no functional gain.
- **Phase 3.C / 4 (Sales section — uniform add-form heights)** done — direct sales, material returns and dispatching now share the bookings add-form contract documented in `UI_MIGRATION_PLAN.md` §11:
  - `templates/direct_sales.html` (≈3,100 ln): both `<style>` blocks rewritten. All `min-height` overrides on `form-control` / `form-select` (the 34 / 36 / 38 px mix that misaligned every row) are gone. Outer fields = Bootstrap default 38 px; line-items = `form-control-sm` 31 px. The 6 `field-label` labels in add + edit modals and the unused `mini-cell-label` rules were replaced by the unified `class="text-white-50 small fw-bold mb-1"` (matches `bookings.html`). Delivery-row inputs/selects (12 sites including the JS `<template id="deliveryPersonRowTemplate">`) and the sale-type / edit-sale-category selects no longer carry `-sm`, so every cell in a row is exactly the same height. Markup `name=`, `id=`, hidden inputs, `{% if %}` branches, and JS hooks unchanged. `templates/direct_sales.html.legacy` retained until Phase 12.
  - `templates/dispatching.html`: stripped the jumbo `py-3 fs-5` / `py-3 fs-4` / `py-3 rounded-pill` overrides from all 5 inputs and the Reset / Confirm buttons (every input was ~64 px tall). Labels normalised to the booking pattern. `action`, `name=` attrs, "Has Bill" toggle JS unchanged.
  - `templates/material_returns.html`: 11 filter / add-return-modal labels normalised to the booking pattern. Line-item grid (`returns-item-row`) already used Bootstrap defaults. Combobox JS, names, conditionals untouched.
  - `UI_MIGRATION_PLAN.md` extended with §10 "Live progress log" (status table for every phase 0–12) and §11 "Uniform add-form pattern" (the 8-rule contract every later phase has to obey). Phases 5–12 still pending — they will reuse this same contract.
  - Per-page validation (Flask test_client, authenticated as admin): `/direct_sales`, `/direct_sales?show=voided`, `/direct_sales?show=all`, `/direct_sales?bill_state=billed&page=1` all return 200. Rendered HTML contains: `id="addSaleModal"`, `modal-dialog ui-sheet-modal` (×12 — 1 add + 11 edit modals), `ds-sale-sheet` (×12), `action="/add_direct_sale"`, `action="/edit_bill/DirectSale/{id}"`, `id="addSaleClientCode"`, `id="addSaleHasBillHidden"`, `id="addSaleDate"`, `id="addSaleDraftId"`, `id="addSaleManualBill"`, `submitHoldSale()`, `openHoldDraftsModal()`, `resetAddSaleForm()`, `Save Sale`, `Save Changes`, every `name="..."` and `name="...[]"` field for delivery_person / qty / unit_rate / product_name / alternate_material / discount / paid_amount / payment_method / payment_account_id, `flatpickr(` JS init, `data-bs-dismiss="modal"`, `data-bs-toggle="modal"` (filter form `name="show"` hidden input also preserved). **Zero** `modal-fullscreen` leftover, **zero** `background: #1e293b` leftover, **zero** `AirDatepicker` references.
