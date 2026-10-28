from fastapi import Request

from app.config import settings


def client_ip(request: Request) -> str:
    """Real client address. X-Forwarded-For is only believed when TRUSTED_PROXY_COUNT says how many
    proxies sit in front of us; otherwise a client could forge it to dodge rate limits."""
    n = settings.trusted_proxy_count
    if n > 0:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if len(hops) >= n:
            return hops[-n]
    return request.client.host if request.client else "unknown"
