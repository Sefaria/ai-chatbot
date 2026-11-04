"""
Identity for logged-out (anonymous) visitors.

The widget creates a random UUID (`anonId`) for a logged-out visitor and sends
it instead of a user token. The server never stores that raw id, only a hash of
it, so a leaked database can't be used to act as the visitor.
"""

import hashlib

ANONYMOUS_USER_ID_PREFIX = "anon:"


def anonymous_user_id(anon_id) -> str:
    """Return the persisted user id for an anonymous visitor's anonId."""
    digest = hashlib.sha256(str(anon_id).encode("utf-8")).hexdigest()
    return ANONYMOUS_USER_ID_PREFIX + digest
