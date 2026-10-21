"""Identity fixture for archive contract tests, never a production bypass."""
from sqlalchemy.engine import Engine
from sqlmodel import Session

from memory.identity_models import AccountRow

OWNER = "a" * 32


def seed_owner(engine: Engine, owner: str = OWNER) -> None:
    with Session(engine) as session:
        if session.get(AccountRow, owner) is None:
            session.add(AccountRow(id=owner, username="fixture-" + owner, password_hash="unusable-test-hash", created_at=0))
            session.commit()
