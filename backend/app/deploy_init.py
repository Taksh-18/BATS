"""First-boot setup for a hosted deployment:  python -m app.deploy_init

Idempotent, so it is safe to run on every start. As the database OWNER it creates the schema if the
database is empty, optionally creates the limited bats_app role (when APP_DB_PASSWORD is set), then
seeds demo data on first boot. The app itself then runs as bats_app.
"""
import asyncio
import os
from pathlib import Path

import asyncpg

from app.config import settings
from app.dburl import APP_ROLE, APP_ROLE_MARKER, normalize

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


async def main() -> None:
    url, connect_args = normalize(settings.database_url)  # the owner's credentials
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1), ssl=connect_args.get("ssl"))
    try:
        if not await conn.fetchval("select to_regclass('public.hospital') is not null"):
            await conn.execute((SQL_DIR / "schema.sql").read_text())
            print("deploy_init: schema created")
        else:
            print("deploy_init: schema already present")

        APP_ROLE_MARKER.unlink(missing_ok=True)
        if settings.app_db_password:
            try:
                exists = await conn.fetchval("select 1 from pg_roles where rolname = $1", APP_ROLE)
                verb = "ALTER" if exists else "CREATE"
                await conn.execute(await conn.fetchval(
                    f"select format('{verb} ROLE {APP_ROLE} LOGIN PASSWORD %L', $1::text)", settings.app_db_password))
                await conn.execute((SQL_DIR / "roles.sql").read_text())
                APP_ROLE_MARKER.write_text("ok")
                print(f"deploy_init: {APP_ROLE} role ready; the app will run with least privilege")
            except asyncpg.PostgresError as exc:
                print(f"deploy_init: WARNING could not set up the {APP_ROLE} role ({type(exc).__name__}: {exc}); "
                      "the app will run as the database owner. The append-only trigger and hash chain still apply.")
        else:
            print("deploy_init: WARNING APP_DB_PASSWORD not set; the app will run as the database owner")
    finally:
        await conn.close()

    if os.environ.get("SEED_ON_FIRST_BOOT", "true").lower() == "true":
        from app import seed  # imported late: it opens the app's own (limited-role) connection
        await seed.main()


if __name__ == "__main__":
    asyncio.run(main())
