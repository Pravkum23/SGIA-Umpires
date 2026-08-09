# SGIA Umpires PWA

Deploy this directory as a Vercel project with **Root Directory** set to `pwa`.

Required environment variable:

- `DATABASE_URL` — the same Supabase PostgreSQL connection string used by Streamlit.

Optional:

- `NEXT_PUBLIC_ADMIN_URL` — the Streamlit `?admin=1` URL, exposed only at `/admin-link`.

No database credential is sent to the browser. Authentication uses the existing PBKDF2 PIN hashes and a secure HttpOnly device cookie.

Run `pnpm install --frozen-lockfile`, `pnpm test`, `pnpm typecheck`, `pnpm lint`, and `pnpm build` before deployment.
