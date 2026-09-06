"""Authentication abstraction with an explicit local demonstration provider."""
from __future__ import annotations
from abc import ABC, abstractmethod
import os

from . import audit
from .domain import ActorContext


ROLE_PERMISSIONS = {
    "health_worker": frozenset({
        "patient:read", "encounter:create", "encounter:read", "encounter:resume",
        "encounter:mutate", "media:create", "media:read", "encounter:export",
    }),
    "doctor": frozenset({
        "patient:read", "encounter:read", "encounter:resume", "encounter:mutate",
        "media:read", "encounter:export",
    }),
    "clinical_reviewer": frozenset({
        "patient:read", "encounter:read", "encounter:resume", "encounter:mutate",
        "media:read", "encounter:export",
    }),
    "dho": frozenset({"district:view"}),
}
ROLE_ALIASES = {"paramedic": "health_worker", "worker": "health_worker",
                "district": "dho", "district_viewer": "dho"}


class AuthenticationError(PermissionError):
    pass


class AuthenticationProvider(ABC):
    @abstractmethod
    def authenticate(self, credential: str) -> ActorContext:
        raise NotImplementedError


class DemoAuthenticationProvider(AuthenticationProvider):
    """Local-only identity chooser; never represents production authentication."""
    source = "demo-local"

    def available_accounts(self) -> list[dict]:
        audit.init_db()
        conn = audit._connect()
        try:
            return [dict(row) for row in conn.execute(
                "SELECT user_id, name, role FROM users ORDER BY name, user_id"
            ).fetchall()]
        finally:
            conn.close()

    def authenticate(self, credential: str) -> ActorContext:
        audit.init_db()
        conn = audit._connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (credential,)).fetchone()
            if not row:
                raise AuthenticationError("Unknown demo identity.")
            memberships = [item[0] for item in conn.execute(
                "SELECT bhu_id FROM user_facilities WHERE user_id=? ORDER BY bhu_id", (credential,)
            ).fetchall()]
            if not memberships and row["bhu_id"]:
                memberships = [row["bhu_id"]]
            if not memberships:
                raise AuthenticationError("The account has no facility membership.")
            role = ROLE_ALIASES.get((row["role"] or "").lower(), (row["role"] or "").lower())
            permissions = ROLE_PERMISSIONS.get(role)
            if permissions is None:
                raise AuthenticationError("The account role is not supported.")
            return ActorContext(row["user_id"], row["name"] or row["user_id"], role,
                                tuple(memberships), memberships[0], permissions, self.source)
        finally:
            conn.close()


class UnavailableProductionProvider(AuthenticationProvider):
    def authenticate(self, credential: str) -> ActorContext:
        raise AuthenticationError(
            "Production identity provider is not configured. Set SMARTSEHAT_AUTH_MODE=demo "
            "only for an explicit local demonstration."
        )


def get_authentication_provider() -> AuthenticationProvider:
    mode = os.getenv("SMARTSEHAT_AUTH_MODE", "demo").strip().lower()
    if mode == "demo":
        return DemoAuthenticationProvider()
    if mode == "production":
        return UnavailableProductionProvider()
    raise AuthenticationError("SMARTSEHAT_AUTH_MODE must be 'demo' or 'production'.")
