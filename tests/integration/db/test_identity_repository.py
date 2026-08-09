"""Integration tests against a real Postgres -- proves the ORM mapping, FK
cascades, unique/check constraints, and CITEXT case-insensitivity actually
behave the way the domain layer assumes, not just what the fakes assume.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from iam_platform.domain.identity.entities import AuthIdentity, IdentityKind, User
from iam_platform.domain.shared.value_objects import Email
from iam_platform.infrastructure.db.models.identity import UserModel

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


def _new_user(email: str) -> User:
    return User(id=uuid4(), email=Email(email), security_stamp=uuid4(), created_at=NOW, updated_at=NOW)


class TestUserRepository:
    async def test_add_then_get_by_id_round_trips(self, uow_factory) -> None:
        user = _new_user("roundtrip@example.com")
        async with uow_factory() as uow:
            await uow.users.add(user)

        async with uow_factory() as uow:
            fetched = await uow.users.get_by_id(user.id)

        assert fetched is not None
        assert str(fetched.email) == "roundtrip@example.com"
        assert fetched.status.value == "pending_verification"

    async def test_email_lookup_is_case_insensitive(self, uow_factory) -> None:
        user = _new_user("CaseSensitive@Example.com")
        async with uow_factory() as uow:
            await uow.users.add(user)

        async with uow_factory() as uow:
            fetched = await uow.users.get_by_email(Email("casesensitive@example.com"))

        assert fetched is not None
        assert fetched.id == user.id

    async def test_duplicate_email_violates_unique_constraint(self, uow_factory) -> None:
        async with uow_factory() as uow:
            await uow.users.add(_new_user("dupe@example.com"))

        with pytest.raises(IntegrityError):
            async with uow_factory() as uow:
                await uow.users.add(_new_user("dupe@example.com"))

    async def test_invalid_status_violates_check_constraint(self, uow_factory) -> None:
        # Bypass the domain enum on purpose, inserting straight through the ORM
        # model, to prove the DB-level CHECK constraint is a real backstop and
        # not merely relying on application-layer enum validation.
        with pytest.raises(IntegrityError):
            async with uow_factory() as uow:
                uow.session.add(
                    UserModel(
                        id=uuid4(),
                        email="badstatus@example.com",
                        status="not_a_real_status",
                        security_stamp=uuid4(),
                    )
                )


class TestCascadingDeletes:
    async def test_deleting_a_user_cascades_to_identities_and_credentials(self, uow_factory) -> None:
        from iam_platform.infrastructure.db.models.identity import CredentialModel, UserModel

        user = _new_user("cascade@example.com")
        identity = AuthIdentity(id=uuid4(), user_id=user.id, kind=IdentityKind.PASSWORD, created_at=NOW)

        async with uow_factory() as uow:
            await uow.users.add(user)
            await uow.identities.add(identity)
            uow.session.add(
                CredentialModel(
                    id=uuid4(),
                    identity_id=identity.id,
                    password_hash="irrelevant",
                    password_updated_at=NOW,
                )
            )

        async with uow_factory() as uow:
            model = await uow.session.get(UserModel, user.id)
            await uow.session.delete(model)

        async with uow_factory() as uow:
            assert await uow.identities.get_by_id(identity.id) is None
            assert await uow.credentials.get_by_identity_id(identity.id) is None
