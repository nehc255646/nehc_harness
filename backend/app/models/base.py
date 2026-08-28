from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass
