"""OpenReview client construction and API facade.

This module wraps :class:`openreview.api.OpenReviewClient` with a thin,
rate-limited facade (:class:`OpenReviewFacade`) that sleeps
``config.request_delay`` seconds before each API call. Nothing here
prints; failures raise the typed exceptions from
:mod:`openreview_cli.exceptions`.
"""

from __future__ import annotations

import time
from typing import Any

from .config import Config
from .exceptions import APIError, AuthenticationError

#: Base URL of the OpenReview API v2.
API_BASE = "https://api2.openreview.net"

try:  # pragma: no cover - import guard for environments without the SDK
    import openreview
    from openreview.api import OpenReviewClient as _SDKClient
except ImportError as _exc:  # pragma: no cover
    openreview = None  # type: ignore[assignment]
    _SDKClient = None  # type: ignore[assignment]
    _IMPORT_ERROR = _exc

try:
    import jwt
except ImportError:  # pragma: no cover - pyjwt is optional at import time
    jwt = None  # type: ignore[assignment]


def create_client(
    config: Config,
    username: str | None = None,
    password: str | None = None,
) -> Any:
    """Create an ``openreview.api.OpenReviewClient``.

    Args:
        config: The loaded configuration (used for error messages only).
        username: Resolved username, or ``None`` for an anonymous
            client.
        password: Resolved password, or ``None`` for an anonymous
            client.

    Returns:
        A configured ``openreview.api.OpenReviewClient`` instance.

    Raises:
        APIError: If the ``openreview`` package is not installed.
        AuthenticationError: If login fails.
    """
    if _SDKClient is None:  # pragma: no cover
        raise APIError(
            "The 'openreview' package is required but not installed"
        ) from _IMPORT_ERROR

    credentials: dict[str, str] = {}

    if username is not None and password is not None:
        credentials = {"username": username, "password": password}

    try:
        return _SDKClient(baseurl=API_BASE, **credentials)
    except Exception as exc:  # noqa: BLE001 - SDK raises varied types
        raise AuthenticationError(
            f"OpenReview login failed for {username or 'anonymous client'}: {exc}"
        ) from exc


def get_profile_id(client: Any) -> str:
    """Determine the logged-in user's profile id.

    API v2 rejects ``get_profile()`` without an explicit id, so the id
    is first decoded from the client's JWT token, then confirmed via
    ``client.get_profile(profile_id)``.

    Args:
        client: An authenticated ``openreview.api.OpenReviewClient``.

    Returns:
        The profile id, e.g. ``~Alice_Doe1``.

    Raises:
        APIError: If ``pyjwt`` is not installed or the token cannot be
            decoded.
        AuthenticationError: If the profile id cannot be determined or
            confirmed.
    """
    if jwt is None:  # pragma: no cover
        raise APIError("The 'pyjwt' package is required to determine the profile id")

    token = getattr(client, "token", None)

    if not token:
        raise AuthenticationError(
            "Cannot determine profile id: the client has no auth token"
        )

    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256"],
        )
    except Exception as exc:  # noqa: BLE001 - jwt raises several types
        raise APIError(f"Could not decode the client's auth token: {exc}") from exc

    user = payload.get("user", {})

    if not isinstance(user, dict):
        raise AuthenticationError("Unexpected JWT payload: 'user' is not a mapping")

    profile_id = (
        (user.get("profile") or {}).get("id")
        if isinstance(user.get("profile"), dict)
        else None
    ) or user.get("id")

    if not profile_id:
        raise AuthenticationError(
            "Cannot determine profile id from the client's auth token"
        )

    try:
        client.get_profile(profile_id)
    except Exception as exc:  # noqa: BLE001
        raise AuthenticationError(
            f"Could not confirm profile {profile_id}: {exc}"
        ) from exc

    return str(profile_id)


class OpenReviewFacade:
    """Rate-limited wrapper around an OpenReview API v2 client.

    Sleeps ``request_delay`` seconds before every delegated API call to
    stay polite to the OpenReview servers. Also caches the logged-in
    user's profile id after the first :meth:`profile_id` call.
    """

    def __init__(self, client: Any, request_delay: float = 0.5) -> None:
        """Initialize the facade.

        Args:
            client: An ``openreview.api.OpenReviewClient``.
            request_delay: Seconds to sleep before each API call.
        """
        self._client = client
        self.request_delay = request_delay
        self._profile_id: str | None = None

    @classmethod
    def from_config(
        cls,
        config: Config,
        username: str | None = None,
        password: str | None = None,
    ) -> "OpenReviewFacade":
        """Build a facade from a :class:`Config` and credentials.

        Args:
            config: The loaded configuration.
            username: Resolved username, or ``None`` for anonymous.
            password: Resolved password, or ``None`` for anonymous.

        Returns:
            A ready-to-use :class:`OpenReviewFacade`.
        """
        client = create_client(config, username, password)
        return cls(client, request_delay=config.request_delay)

    @property
    def client(self) -> Any:
        """The underlying SDK client."""
        return self._client

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke an SDK method after sleeping ``request_delay``."""
        method = getattr(self._client, method_name, None)

        if method is None or not callable(method):
            raise APIError(
                f"The OpenReview client does not expose '{method_name}'"
            )

        # Sleep only after the existence check, so a misconfigured
        # client fails fast instead of paying the delay first.
        time.sleep(self.request_delay)

        return method(*args, **kwargs)

    def profile_id(self) -> str:
        """Return the logged-in user's profile id (cached).

        Raises:
            AuthenticationError: If the profile id cannot be determined.
        """
        if self._profile_id is None:
            self._profile_id = get_profile_id(self._client)

        return self._profile_id

    def get_all_notes(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_all_notes`` (rate limited)."""
        return self._call("get_all_notes", *args, **kwargs)

    def get_group(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_group`` (rate limited)."""
        return self._call("get_group", *args, **kwargs)

    def get_groups(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_groups`` (rate limited)."""
        return self._call("get_groups", *args, **kwargs)

    def get_invitation(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_invitation`` (rate limited)."""
        return self._call("get_invitation", *args, **kwargs)

    def get_invitations(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_invitations`` (rate limited)."""
        return self._call("get_invitations", *args, **kwargs)

    def get_profile(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_profile`` (rate limited)."""
        return self._call("get_profile", *args, **kwargs)

    def get_pdf(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_pdf`` (rate limited)."""
        return self._call("get_pdf", *args, **kwargs)

    def get_attachment(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_attachment`` (rate limited)."""
        return self._call("get_attachment", *args, **kwargs)

    def get_note(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_note`` (rate limited)."""
        return self._call("get_note", *args, **kwargs)

    def get_edges(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to ``client.get_edges`` (rate limited)."""
        return self._call("get_edges", *args, **kwargs)
