"""Turns a hosting provider's DATABASE_URL into what SQLAlchemy/asyncpg need."""
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

APP_ROLE = "bats_app"
# Written by deploy_init only after the limited role was really set up. The app uses the role only
# when this exists, so a host that won't let us create roles degrades to "run as owner", not to a crash.
APP_ROLE_MARKER = Path(__file__).resolve().parents[1] / ".app_role_ready"


def normalize(url: str, app_role_password: str = "") -> tuple[str, dict]:
    """Returns (sqlalchemy_url, connect_args).

    - accepts postgres:// and postgresql:// (what Render, Neon, Heroku give you)
    - asyncpg doesn't understand ?sslmode=, so it becomes connect_args
    - if app_role_password is set, connects as the limited bats_app role instead of the owner
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    connect_args: dict = {}
    if query.pop("sslmode", "disable") in ("require", "verify-ca", "verify-full"):
        connect_args["ssl"] = "require"
    netloc = parts.netloc
    if app_role_password:
        host = parts.hostname or "localhost"
        netloc = f"{APP_ROLE}:{quote(app_role_password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(("postgresql+asyncpg", netloc, parts.path, urlencode(query), "")), connect_args
