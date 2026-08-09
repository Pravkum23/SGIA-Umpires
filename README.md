# SGIA Umpires

Mobile-first availability collection and explainable umpire/scorer allocation for SGIA. It is an administrative aid and does **not** produce an official rating.

## Run locally

```bash
python -m venv .venv
pip install -r requirements.txt
$env:SGIA_ADMIN_PIN="replace-me"
streamlit run app.py
```

Without a production database URL, the app uses `sgia_umpires.db` for local development only. The 29 fixtures and 16 initial people are seeded idempotently. Slots are closed by default.

## Streamlit Community Cloud

Deploy repository `Pravkum23/SGIA-Umpires`, branch `main`, entry point `app.py`. The normal URL is the volunteer-only poll. Append `?admin=1` for the PIN-protected console. In **App settings → Secrets**, add:

```toml
SGIA_ADMIN_PIN = "a-long-private-pin"
DATABASE_URL = "postgresql://postgres.PROJECT:PASSWORD@HOST:5432/postgres?sslmode=require"
```

Use the Supabase transaction-pooler or direct Postgres connection string. Escape special password characters as URL encoding. Never commit the values. `SUPABASE_DB_URL` is accepted as an alternative to `DATABASE_URL`. The app initializes the schema automatically; `schema.sql` is supplied for explicit provisioning.

## Architecture

- `app.py`: public poll and PIN-protected admin UI
- `src/db.py`: SQLAlchemy persistence and idempotent initialization
- `src/allocation.py`: availability/eligibility-first explainable proposal engine
- `src/board.py`: editable three-role allocation board and branded PNG export
- `src/lifecycle.py`: remembered-device tokens, published duties, withdrawals, and replacements
- `src/seed.py`: traceable fixture and people seed data
- `schema.sql`: Postgres/Supabase schema
- `tests/`: persistence, rules, updates, seed, and output checks

Availability follows `OPEN → FROZEN → PUBLISHED`: open responses are editable and audited, frozen responses are read-only during allocation, and published confirmed duties are visible to remembered volunteers. A withdrawal creates a visible replacement-required assignment without silently regenerating confirmed work. Each match supports Umpire 1, Umpire 2, and Scorer. The final allocation board supports manual edits plus PNG, CSV, and WhatsApp-ready exports. Administrators remain responsible for reviewing and confirming all proposed allocations.
