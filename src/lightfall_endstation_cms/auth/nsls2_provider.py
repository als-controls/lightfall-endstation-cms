"""NSLS-II tiled authentication for the CMS deployment.

The login dialog collects the user's BNL username + password (masked) and this
provider exchanges them for a tiled token via the server's password grant
(which triggers the user's Duo push). The password is sent only to the tiled
auth endpoint over HTTPS and is never stored by Lightfall. The token is cached
to disk (remember_me), so the profile-collection's later
``from_profile("nsls2")`` reads resolve from that warm cache with no re-prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from lightfall.auth.providers.base import AuthProvider
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin

TILED_URI = "https://tiled.nsls2.bnl.gov"
# The profile-collection reads via from_profile("nsls2"); warm the token cache
# through the SAME accessor so the cache key matches and the profile's later
# from_profile("nsls2") reads ride it (rather than from_uri(TILED_URI), which
# could key the cache differently if the profile URI normalizes differently).
TILED_PROFILE = "nsls2"


class NSLS2TiledAuthProvider(AuthProvider):
    """Auth provider that logs into NSLS-II tiled (Duo) and warms the token cache."""

    @property
    def name(self) -> str:
        return "nsls2_tiled"

    @property
    def supports_password_auth(self) -> bool:
        return True  # username + password (masked) collected in the login dialog

    @property
    def supports_browser_auth(self) -> bool:
        return False

    @staticmethod
    def _select_password_provider(providers: list) -> Any:
        """Pick the internal/password auth provider from the server's list.

        Raises RuntimeError if there is none — rather than silently falling back
        to providers[0] (which could be an OAuth provider, sending credentials
        to the wrong endpoint) or indexing an empty list (IndexError).
        """
        spec = next(
            (p for p in providers if getattr(p, "mode", None) in ("internal", "password")),
            None,
        )
        if spec is None:
            modes = [getattr(p, "mode", None) for p in providers]
            raise RuntimeError(
                f"No internal/password auth provider at {TILED_URI}; got modes {modes}"
            )
        return spec

    def _tiled_login(self, username: str, password: str) -> bool:
        """Exchange username+password for a tiled token. BEAMLINE SEAM.

        THIS IS THE LIVE PRODUCTION IMPLEMENTATION. Against the real NSLS-II
        tiled server this drives the server's password grant with the
        credentials the user typed into the login dialog (which triggers their
        Duo push) and blocks until it resolves. Tests override this method.

        Implementation note (tiled >=0.2): this replicates the non-interactive
        part of ``tiled.client.context.prompt_for_credentials`` — select the
        internal/password provider, call ``password_grant`` with the supplied
        credentials, then ``context.configure_auth(tokens, remember_me=True)``
        to cache the token. This avoids tiled's terminal prompt entirely. The
        provider mode/structure must be confirmed against the real NSLS-II
        tiled instance at the beamline (spec §9, open item 1).

        Returns True if a token was obtained and cached; False otherwise.
        """
        from tiled.client import from_profile
        from tiled.client.context import password_grant

        # Use the same profile the profile-collection reads with, so the token
        # we cache here is the one its from_profile("nsls2") will reuse.
        client = from_profile(TILED_PROFILE)
        context = client.context
        providers = context.server_info.authentication.providers
        spec = self._select_password_provider(providers)
        tokens = password_grant(
            context.http_client,
            spec.links["auth_endpoint"],
            spec.provider,
            username,
            password,
        )
        context.configure_auth(tokens, remember_me=True)
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

        if not username or not password:
            logger.warning("NSLS-II login requires a username and password")
            return None

        try:
            ok = self._tiled_login(username, password)
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
            expires_at=datetime.now(UTC) + timedelta(days=7),  # PLACEHOLDER TTL — not derived from actual NSLS-II tiled token expiry; reconcile at beamline
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
        return True

    def create_provider(self) -> NSLS2TiledAuthProvider:
        return NSLS2TiledAuthProvider()
