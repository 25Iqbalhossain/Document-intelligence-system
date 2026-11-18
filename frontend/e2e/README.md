# Browser end-to-end check

Drives real Chromium through the whole UI. Needs the full stack running (API on :8000, Celery worker, Postgres, Redis,
`npm run dev` on :5173) and an admin `ui-admin@example.com` with password `e2e-admin-password-1`
(`DOCINT_ADMIN_PASSWORD=e2e-admin-password-1 python scripts/create_admin.py ui-admin@example.com`).

    mkdir -p shots && NODE_PATH=$(npm root -g) node e2e.mjs "$PWD"      # needs `playwright` installed

It writes screenshots to `shots/`, prints PASS/FAIL per step, and leaves its test documents in the database,
so run it against a development database only. Not wired into CI yet.
