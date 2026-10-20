from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models
from backend import schemas


def get_user_by_username(db: Session, username: str) -> models.Users | None:
    stmt = select(models.Users).where(models.Users.username == username)
    return db.scalars(stmt).first()


def create_user(db: Session, username: str, password_hash: str) -> models.Users:
    user = models.Users(username=username, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
