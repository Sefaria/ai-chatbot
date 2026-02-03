"""Tests for Actor dataclass."""

import pytest

from chat.auth import Actor


class TestActor:
    """Test Actor dataclass."""

    def test_user_actor_creation(self):
        """Test creating an actor with user_id."""
        actor = Actor(user_id="user123")
        assert actor.user_id == "user123"
        assert actor.service_id is None
        assert actor.is_user is True
        assert actor.is_service is False
        assert actor.identity == "user123"

    def test_service_actor_creation(self):
        """Test creating an actor with service_id."""
        actor = Actor(service_id="braintrust")
        assert actor.user_id is None
        assert actor.service_id == "braintrust"
        assert actor.is_user is False
        assert actor.is_service is True
        assert actor.identity == "service:braintrust"

    def test_actor_requires_identity(self):
        """Test that actor requires at least one identity."""
        with pytest.raises(ValueError, match="must have user_id or service_id"):
            Actor()

    def test_actor_rejects_both_identities(self):
        """Test that actor cannot have both user_id and service_id."""
        with pytest.raises(ValueError, match="cannot have both"):
            Actor(user_id="user123", service_id="braintrust")

    def test_to_db_fields_for_user(self):
        """Test to_db_fields returns correct dict for user actor."""
        actor = Actor(user_id="user123")
        fields = actor.to_db_fields()
        assert fields == {"user_id": "user123", "service_id": None}

    def test_to_db_fields_for_service(self):
        """Test to_db_fields returns correct dict for service actor."""
        actor = Actor(service_id="braintrust")
        fields = actor.to_db_fields()
        assert fields == {"user_id": None, "service_id": "braintrust"}
