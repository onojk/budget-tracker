# Future Improvements

Captured from external code review. Prioritized for future refactor sessions.

## High Priority

- **Make `/budget-summary` data-driven from DB** instead of hardcoded constants in the route
- **Migrate `Transaction.amount` from `Float` to `Numeric(12,2)`** to avoid floating-point rounding errors
- **Update `README.md`** to reflect current state: test count, active parsers, dashboard features
- **Verify no private data** in backup zips or git history (statements, account numbers, balances)

## Medium Priority

- **Split `app.py`** into routes / services / utils structure (currently ~1,100 lines)
- **Replace magic account IDs** (e.g. `_CC_IDS = [5, 6, 7, 8, 9]`) with account-type queries
- **Adopt Alembic / Flask-Migrate** for schema changes instead of one-off migration scripts
- **Improve PayPal parser coverage** — currently Phase A only (Mass Pay + Non Reference Credit)
- **Better OCR error logging** for rejected lines — surface rejection reasons, not just counts

## Lower Priority / Nice-to-Have

- Server-side filtering and pagination on the transactions page
- Multi-select bulk actions on transactions (bulk categorize, bulk delete)
- Year-over-year comparison cards on the dashboard
- Export full backup route (transactions + accounts as CSV/JSON)

## Not Prioritized

- Interactive sliders / what-if simulator — feature creep for this use case
- Mobile responsive layout — localhost only, not an actual use case
- Auth / PIN gate — localhost only
- Dark mode — not needed
