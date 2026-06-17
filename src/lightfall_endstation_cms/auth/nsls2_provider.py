"""NSLS-II tiled/Duo authentication for the CMS deployment.

Authenticating here performs the tiled login against tiled.nsls2.bnl.gov,
which fires a Duo push and caches the resulting token on disk. Because tiled
keys its token cache by server, the profile-collection's later
``from_profile("nsls2")`` reads resolve from that warm cache with no second
Duo push.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from lightfall.auth.providers.base import AuthProvider
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin

TILED_URI = "https://tiled.nsls2.bnl.gov"
TILED_PROFILE = "nsls2"


class NSLS2TiledAuthProvider(AuthProvider):
    """Auth provider that logs into NSLS-II tiled (Duo) and warms the token cache."""

    @property
    def name(self) -> str:
        return "nsls2_tiled"

    @property
    def supports_password_auth(self) -> bool:
        return False  # username + Duo push, no password typed into Lightfall

    @property
    def supports_browser_auth(self) -> bool:
        return False

    def _tiled_login(self, username: str) -> bool:
        """Perform the interactive tiled login (Duo). BEAMLINE SEAM.

        Returns True if a token was obtained and cached. Overridden in tests.
        Finalize against the real provider type during Acceptance (spec §9).

        Reference implementation (confirm at beamline)::

            from tiled.client import from_uri
            client = from_uri(TILED_URI, username=username or None)
            # touching the client forces auth; token caches to disk
            _ = client.context
            return True
        """
        from tiled.client import from_uri

        client = from_uri(TILED_URI, username=username or None)
        _ = client.context  # force the auth handshake (Duo) + token cache
        logger.info("NSLS-II tiled login complete for '{}'", username)
        return True

    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Any | None:
        from lightfall.auth.policy import Role
        from lightfall.auth.session import Session, User

        if not username:
            logger.warning("NSLS-II login requires a username")
            return None

        try:
            ok = self._tiled_login(username)
        except Exception:
            logger.exception("NSLS-II tiled login failed")
            return None
        if not ok:
            return None

        # Real API: User.roles is set[Role], not a singular role= kwarg.
        # The brief's role=Role.USER is adjusted to roles={Role.USER}.
        user = User(
            username=username,
            display_name=username,
            roles={Role.USER},
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        return Session(user=user)

    async def logout(self, session: Any) -> None:
        return None

    async def refresh(self, session: Any) -> Any | None:
        return session

    async def check_connectivity(self) -> bool:
        return True


class NSLS2AuthPlugin(AuthProviderPlugin):
    """Plugin that contributes the NSLS-II (CMS) login to the login dialog."""

    @property
    def name(self) -> str:
        return "nsls2_tiled"

    @property
    def display_name(self) -> str:
        return "NSLS-II (CMS)"

    @property
    def requires_username(self) -> bool:
        return True

    @property
    def requires_password(self) -> bool:
        return False

    def create_provider(self) -> NSLS2TiledAuthProvider:
        return NSLS2TiledAuthProvider()
