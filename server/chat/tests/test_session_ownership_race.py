"""create_or_get_session: the ownership check and the write happen under one row lock."""

import threading

import pytest
from django.db import connection

from chat.auth import Actor
from chat.models import ChatSession
from chat.V2.services.session_service import SessionOwnershipError, create_or_get_session


def test_existing_session_of_another_actor_is_refused_and_unchanged(db):
    create_or_get_session("sess-race-1", Actor(user_id="owner-a"))
    with pytest.raises(SessionOwnershipError):
        create_or_get_session("sess-race-1", Actor(user_id="intruder-b"))
    assert ChatSession.objects.get(session_id="sess-race-1").user_id == "owner-a"


def test_owner_gets_the_same_session_back(db):
    first, created = create_or_get_session("sess-race-2", Actor(user_id="owner-a"))
    again, created_again = create_or_get_session("sess-race-2", Actor(user_id="owner-a"))
    assert (created, created_again) == (True, False)
    assert again.pk == first.pk


def test_extra_defaults_apply_on_reuse(db):
    create_or_get_session("sess-race-3", Actor(user_id="owner-a"))
    session, _ = create_or_get_session("sess-race-3", Actor(user_id="owner-a"), is_deleted=True)
    assert session.is_deleted is True


@pytest.mark.django_db(transaction=True)
def test_concurrent_callers_never_take_over_a_session():
    """Twenty threads, two actors, one new session id: exactly one actor owns it at the end and
    every call by the other actor is refused."""
    if connection.vendor != "postgresql":
        pytest.skip("needs row locks")
    barrier = threading.Barrier(20)
    outcomes = []

    def call(user_id):
        try:
            barrier.wait()
            create_or_get_session("sess-race-concurrent", Actor(user_id=user_id))
            outcomes.append((user_id, "ok"))
        except SessionOwnershipError:
            outcomes.append((user_id, "refused"))
        finally:
            connection.close()

    threads = [threading.Thread(target=call, args=(f"actor-{i % 2}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    owner = ChatSession.objects.get(session_id="sess-race-concurrent").user_id
    assert len(outcomes) == 20
    assert all(result == "ok" for user, result in outcomes if user == owner)
    assert all(result == "refused" for user, result in outcomes if user != owner)
