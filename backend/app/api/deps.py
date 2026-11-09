import uuid
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from .. import config
from ..db.models import User
from ..db.session import get_session

DEV_USER_EMAIL = "dev@localhost"     # the default; config.DEV_USER_EMAIL can point at another user
VIEW_COOKIE = "view"                 # "plaid": the dev user with linked banks, in place of the configured demo user


# whose data this request reads. with the admin tools on, the "view" cookie can swap the configured (demo) user for
# the default dev user, the one Plaid links banks to, so a demo can show both without editing .env and restarting
def current_email(request: Request) -> str:
    if config.ENABLE_ADMIN and request.cookies.get(VIEW_COOKIE) == "plaid":
        return DEV_USER_EMAIL
    return config.DEV_USER_EMAIL


# stand-in until the auth ticket: every request is one configured user (config.DEV_USER_EMAIL, read per request).
# only the default dev user is created on first use, so a typo in the setting can't make a stray user.
# replace this function with real auth; routes don't need to change.
def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    email = current_email(request)
    user = session.scalar(select(User).where(User.email == email))
    if user is None and email != DEV_USER_EMAIL:
        raise HTTPException(404, "Unknown DEV_USER_EMAIL user.")
    if user is None:
        # "!" is not a valid hash, so nobody can log in as this user
        user = User(id=str(uuid.uuid4()), email=DEV_USER_EMAIL, password_hash="!")
        session.add(user)
        session.commit()
    return user
