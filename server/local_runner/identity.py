"""Resolving a browser's user token into the identity the runner stores.

The daemon cannot decrypt a userId token — that needs CHATBOT_USER_TOKEN_SECRET,
which is a server secret and would let whoever held it mint tokens for any user.
So pairing asks our server to do it once, and the answer is stored in the pairing
record. Later requests are authorised by the runner token alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

DEFAULT_SERVER = "https://chatbot.sefaria.org"
IDENTITY_PATH = "/api/v2/local/identity"
TIMEOUT_SECONDS = 10


class IdentityError(RuntimeError):
    """The server refused or could not resolve the presented token."""


@dataclass(frozen=True)
class Identity:
    user_id: str
    sefaria_user_id: str | None


def server_base_url() -> str:
    return os.environ.get("SEFARIA_CHATBOT_URL", DEFAULT_SERVER).rstrip("/")


def resolve(encrypted_user_token: str) -> Identity:
    """Ask our server who this token belongs to."""
    url = f"{server_base_url()}{IDENTITY_PATH}"
    try:
        response = httpx.post(
            url,
            json={"userId": encrypted_user_token},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise IdentityError(f"could not reach {url}: {exc}") from exc

    if response.status_code == 401:
        raise IdentityError("the server rejected that userId token")
    if response.status_code != 200:
        raise IdentityError(f"unexpected status {response.status_code} from {url}")

    payload = response.json()
    user_id = payload.get("userId")
    if not user_id:
        raise IdentityError("server response had no userId")

    return Identity(user_id=user_id, sefaria_user_id=payload.get("sefariaUserId"))
