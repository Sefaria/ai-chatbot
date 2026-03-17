"""Tests for Actor dataclass."""

from chat.auth import Actor


class TestActor:
    """Test Actor dataclass."""

    def test_user_actor_creation(self):
        """Test creating an actor with user_id."""
        actor = Actor(user_id="user123")
        assert actor.user_id == "user123"
        assert actor.identity == "user123"
        assert actor.encrypted_token is None

    def test_actor_can_retain_encrypted_token(self):
        """Authenticated actor should be able to carry the original encrypted token."""
        actor = Actor(user_id="user123", encrypted_token="encrypted-token")
        assert actor.encrypted_token == "encrypted-token"

    def test_to_db_fields(self):
        """Test to_db_fields returns correct dict."""
        actor = Actor(user_id="user123")
        fields = actor.to_db_fields()
        assert fields == {"user_id": "user123"}
