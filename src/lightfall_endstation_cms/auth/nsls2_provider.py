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

from lightfall.auth.providers.base import AuthProvider
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin
from lightfall.utils.threads import invoke_in_main_thread
from loguru import logger

TILED_URI = "https://tiled.nsls2.bnl.gov"
# The profile-collection reads via from_profile("nsls2"); warm the token cache
# through the SAME accessor so the cache key matches and the profile's later
# from_profile("nsls2") reads ride it (rather than from_uri(TILED_URI), which
# could key the cache differently if the profile URI normalizes differently).
TILED_PROFILE = "nsls2"
# Tiled node the data browser opens (read-scoped). CMS data lives under
# cms/raw; adjust here if the browse root should differ.
_BROWSE_PATH = ("cms", "raw")
# Service/admin API key the data browser reads through. This is the SAME key
# 00-startup.py builds ``tiled_writing_client`` with, and it is the read
# identity the browser MUST use — see ``_adopt_browser_client`` for why a
# per-user Duo identity lists zero records.
_READ_API_KEY_ENV = "TILED_BLUESKY_WRITING_API_KEY_CMS"


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

    def _tiled_login(self, username: str, password: str) -> Any | None:
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

        Returns the authenticated tiled client on success (so the caller can
        reuse this exact duo-authenticated session for the data browser instead
        of rebuilding an anonymous one); returns None on failure.
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
        # Return the *authenticated* client. Its child nodes share this context,
        # so navigating client["cms"]["raw"] later stays authenticated.
        return client

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
            client = self._tiled_login(username, password)
        except Exception:
            logger.exception("NSLS-II tiled login failed")
            return None
        if not client:
            return None

        # The login just produced a duo-authenticated tiled client. Hand
        # lightfall's data browser a read-scoped NSLS-II catalog by reusing
        # *that same authenticated client* (not a fresh from_profile(), which
        # would be anonymous and list zero runs) so it does not fall back to the
        # default (ALS) Tiled server. Best-effort.
        self._adopt_browser_client(client)

        # Real API: User.roles is set[Role], not a singular role= kwarg.
        # The brief's role=Role.USER is adjusted to roles={Role.USER}.
        user = User(
            username=username,
            display_name=username,
            roles={Role.USER},
            expires_at=datetime.now(UTC) + timedelta(days=7),  # PLACEHOLDER TTL — not derived from actual NSLS-II tiled token expiry; reconcile at beamline
        )
        return Session(user=user)

    def _adopt_browser_client(self, client: Any) -> None:
        """Hand lightfall's data browser the admin-key NSLS-II reading catalog.

        The data browser reads through :class:`TiledService`; without this it
        falls back to ``DEFAULT_TILED_URL`` (the ALS server) and cannot reach
        NSLS-II data.

        CRITICAL — read identity. The browse node is opened with the CMS
        service/admin API key (``TILED_BLUESKY_WRITING_API_KEY_CMS``), NOT the
        per-user Duo identity carried by ``client``. Tiled enforces a
        *per-entry* access policy: every run stores an ``access_blob`` (stamped
        by AccessStamper) and the server filters out any entry that does not
        authorize the *reading principal*. A per-user Duo identity (e.g.
        ``rpandolfi``) authorizes none of the millions of existing ``cms/raw``
        records, so reading through it lists **zero** runs even though the user
        holds the global ``read:data``/``read:metadata`` scopes — this is the
        empty-browser symptom (``<Catalog {}>``, "Loaded 0 of 0 records"). The
        service principal behind the admin key bypasses the per-entry policy
        and sees every record. The Duo login is still required: it
        authenticates the operator and warms the token cache for the write
        path; it simply must not be the browser's *read* identity.

        Open the read node here (on the login worker thread — a network call)
        and adopt it into ``TiledService`` on the GUI thread (``adopt_client``
        starts a QTimer). Adopting runs *before* the ``AUTHENTICATED``
        transition, and ``adopt_client`` flips TiledService's ``auth_mode`` to
        ``NONE`` — so its own session-driven connect (pointed at the default
        server) early-returns instead of clobbering this client.

        Best-effort: a browser that cannot connect must never fail the login.
        If the service key is absent (it is required by 00-startup, so this
        should not happen in a real session) we fall back to the Duo ``client``
        — read-filtered, but still pointed at NSLS-II rather than the ALS
        default — with a loud warning.
        """
        import os

        node = None
        api_key = os.environ.get(_READ_API_KEY_ENV)
        if api_key:
            try:
                from tiled.client import from_uri

                node = from_uri(TILED_URI, api_key=api_key)
                for key in _BROWSE_PATH:
                    node = node[key]
            except Exception as exc:
                logger.warning(
                    "NSLS-II data browser: could not open admin-key tiled {}: {}",
                    "/".join(_BROWSE_PATH),
                    exc,
                )
                node = None
        else:
            logger.warning(
                "NSLS-II data browser: {} is not set; falling back to the "
                "per-user Duo identity, which the per-entry access policy "
                "filters to zero records",
                _READ_API_KEY_ENV,
            )

        if node is None:
            # Last resort (no service key): the Duo-authenticated login client.
            # Read-filtered to the user's own entries, but better than leaving
            # the browser pointed at the ALS default server.
            try:
                node = client
                for key in _BROWSE_PATH:
                    node = node[key]
            except Exception as exc:
                logger.warning(
                    "NSLS-II data browser: could not open tiled {}: {}",
                    "/".join(_BROWSE_PATH),
                    exc,
                )
                return

        def _adopt() -> None:
            try:
                from lightfall.services.tiled_service import TiledService

                TiledService.get_instance().adopt_client(node, url=TILED_URI)
                logger.info(
                    "NSLS-II data browser adopted tiled {}", "/".join(_BROWSE_PATH)
                )
            except Exception as exc:
                logger.warning("NSLS-II data browser: adopt_client failed: {}", exc)

        invoke_in_main_thread(_adopt)

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
    def accent_color(self) -> str:
        return "#2e7d32"  # green

    @property
    def requires_username(self) -> bool:
        return True

    @property
    def requires_password(self) -> bool:
        return True

    def create_provider(self) -> NSLS2TiledAuthProvider:
        return NSLS2TiledAuthProvider()
