# Future Improvements

Captured from AI code review feedback on 2026-04-27. Not prioritized for
immediate work.

## High priority (next refactor session)
- Make /budget-summary data-driven from DB instead of hardcoded constants
- Migrate Transaction.amount from Float to Numeric(12,2) for precision
- Update README.md to reflect current state
- Verify no private data in any backup zips or git history

## Medium priority
- Split app.py into routes/services/utils structure
- Replace magic account IDs with account_type queries
- Adopt Alembic/Flask-Migrate for schema changes
- Improve PayPal parser coverage
- Better OCR error logging for rejected lines

## Lower priority
- Server-side filtering/pagination on transactions
- Year-over-year comparison cards
- Export full backup route

## Not prioritized
- Interactive sliders / what-if simulator (feature creep)
- Mobile responsive (not actual use case)
- Auth/PIN (localhost only)
- Dark mode
