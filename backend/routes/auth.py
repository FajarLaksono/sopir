# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
# pylint: disable=no-member

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import schemas
from backend import handlers
from backend.handlers import auth as auth
from backend.handlers import users as users
from backend.database import get_db
from backend.models import Users

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.UserRead, status_code=201)
def register(data: schemas.UserCreate, db: Session = Depends(get_db)):
    if users.get_user_by_username(db, data.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    pw_hash = auth.hash_password(data.password)
    return users.create_user(db, data.username, pw_hash)


@router.post("/login", response_model=schemas.Token)
def login(data: schemas.UserCreate, db: Session = Depends(get_db)):
    user = users.get_user_by_username(db, data.username)
    if user is None or not auth.verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    return schemas.Token(access_token=auth.create_access_token(user.id))


@router.get("/me", response_model=schemas.UserRead)
def me(user: Users = Depends(auth.get_current_user)):
    return user
