"""API keys and caller identity.

Roles: anonymous (only when keys are not required), client, admin.
Only admins may force a specific model; nobody can raise the server's limits.
"""
from dataclasses import dataclass

from fastapi import Request

from .config import Settings


@dataclass
class Caller:
    role: str          # anonymous | client | admin
    identity: str      # what rate limits count against: the key if given, else the IP


class AuthError(Exception):
    pass


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def identify(request: Request, settings: Settings) -> Caller:
    header = request.headers.get("authorization", "")
    key = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if key and key in settings.admin_keys:
        return Caller("admin", f"key:{key}")
    if key and key in settings.client_keys:
        return Caller("client", f"key:{key}")
    if key:
        raise AuthError("Invalid API key.")
    if settings.require_api_key:
        raise AuthError("An API key is required: send 'Authorization: Bearer <key>'.")
    return Caller("anonymous", f"ip:{client_ip(request, settings.trust_proxy_headers)}")
