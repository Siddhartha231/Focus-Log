"""The sync API.

    POST /v1/auth/register   create an account
    POST /v1/auth/login      get a token for this device
    POST /v1/auth/logout     throw the token away
    POST /v1/sync/push       upload changed sessions
    GET  /v1/sync/pull       download changes after a cursor
    GET  /healthz            is the server (and its database) up?
"""

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from focuslog import __version__
from focuslog.server import schemas, security, settings
from focuslog.server.models import Base, StudySession, Token, User


def create_app(database_url=None) -> FastAPI:
    url = settings.normalize_database_url(database_url) if database_url else settings.database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # fine for a project this size; a bigger one would use Alembic migrations
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="FocusLog sync", version=__version__, lifespan=lifespan)

    # --- dependencies ---

    def get_db():
        with SessionLocal() as session:
            yield session

    def current_token(authorization: str = Header(default=""),
                      db: Session = Depends(get_db)) -> Token:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value:
            raise HTTPException(401, "missing token")
        token = db.scalar(select(Token).where(Token.token_hash == security.hash_token(value)))
        if token is None:
            raise HTTPException(401, "invalid token")
        return token

    # --- health ---

    @app.get("/healthz")
    def healthz(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    # --- accounts ---

    @app.post("/v1/auth/register", status_code=201)
    def register(body: schemas.Credentials, db: Session = Depends(get_db)):
        if not settings.registration_open():
            raise HTTPException(403, "registration is closed on this server")
        if db.scalar(select(User).where(User.email == body.email)) is not None:
            raise HTTPException(409, "that email already has an account")
        db.add(User(email=body.email, password_hash=security.hash_password(body.password)))
        db.commit()
        return {"email": body.email}

    @app.post("/v1/auth/login", response_model=schemas.TokenResponse)
    def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
        user = db.scalar(select(User).where(User.email == body.email))
        # same error for "no such user" and "wrong password", so the API
        # doesn't reveal which emails have accounts
        if user is None or not security.verify_password(body.password, user.password_hash):
            raise HTTPException(401, "wrong email or password")
        token = security.new_token()
        db.add(Token(user_id=user.id, token_hash=security.hash_token(token),
                     device_name=body.device_name))
        db.commit()
        return {"token": token}

    @app.post("/v1/auth/logout", status_code=204)
    def logout(token: Token = Depends(current_token), db: Session = Depends(get_db)):
        db.delete(token)
        db.commit()
        return Response(status_code=204)

    # --- sync ---

    @app.post("/v1/sync/push", response_model=schemas.PushResponse)
    def push(body: schemas.PushRequest, token: Token = Depends(current_token),
             db: Session = Depends(get_db)):
        # Lock this user's row for the whole transaction. Two devices pushing
        # at the same moment then take turns, so seq numbers are handed out in
        # commit order and a pull can never skip over a change.
        user = db.scalar(select(User).where(User.id == token.user_id).with_for_update())

        ids = [s.id for s in body.sessions]
        existing = {}
        if ids:
            rows = db.scalars(select(StudySession).where(
                StudySession.user_id == user.id, StudySession.id.in_(ids)))
            existing = {row.id: row for row in rows}

        accepted = 0
        ignored = 0
        for incoming in body.sessions:
            row = existing.get(incoming.id)
            if row is not None and incoming.updated_at <= row.updated_at:
                # we already have this version or a newer one (a retry, or a
                # stale edit from another device): last write wins
                ignored += 1
                continue

            user.change_seq += 1
            if row is None:
                row = StudySession(user_id=user.id, id=incoming.id)
                db.add(row)
                existing[incoming.id] = row
            row.subject = incoming.subject
            row.started_at = incoming.started_at
            row.duration_seconds = incoming.duration_seconds
            row.status = incoming.status
            row.updated_at = incoming.updated_at
            row.deleted = incoming.deleted
            row.seq = user.change_seq
            accepted += 1

        db.commit()
        return {"accepted": accepted, "ignored": ignored, "cursor": user.change_seq}

    @app.get("/v1/sync/pull", response_model=schemas.PullResponse)
    def pull(since: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=1000),
             token: Token = Depends(current_token), db: Session = Depends(get_db)):
        # ask for one extra row to find out whether there is another page
        rows = db.scalars(
            select(StudySession)
            .where(StudySession.user_id == token.user_id, StudySession.seq > since)
            .order_by(StudySession.seq)
            .limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        cursor = rows[-1].seq if rows else since
        sessions = [
            schemas.SessionPayload(
                id=r.id, subject=r.subject, started_at=r.started_at,
                duration_seconds=r.duration_seconds, status=r.status,
                updated_at=r.updated_at, deleted=r.deleted)
            for r in rows
        ]
        return {"sessions": sessions, "cursor": cursor, "has_more": has_more}

    return app


def _init_sentry() -> None:
    # optional error tracking: only switches on if SENTRY_DSN is set
    dsn = settings.sentry_dsn()
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.init(dsn=dsn, release="focuslog@" + __version__, send_default_pii=False)


_init_sentry()
app = create_app()


def run() -> None:
    """Entry point for the `focuslog-server` command."""
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("focuslog.server.app:app", host=host, port=port)
