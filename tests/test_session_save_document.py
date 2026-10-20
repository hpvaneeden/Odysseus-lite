"""Regression coverage for saving a chat as a versioned transcript document."""

import tempfile
import uuid
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.session_routes as sr
from core.database import Document, DocumentVersion
from core.database import Session as DbSession


_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def test_save_chat_as_document_creates_document_and_first_version(monkeypatch):
    session_id = "session-" + uuid.uuid4().hex
    db = _TS()
    try:
        db.add(DbSession(
            id=session_id,
            owner="alice",
            name="Test chat",
            endpoint_url="http://localhost",
            model="test-model",
        ))
        db.commit()
    finally:
        db.close()

    chat = SimpleNamespace(
        id=session_id,
        owner="alice",
        name="Test chat",
        model="test-model",
        history=[
            SimpleNamespace(role="user", content="Hello", metadata={}),
            SimpleNamespace(role="assistant", content="Hi there.", metadata={}),
        ],
    )
    manager = SimpleNamespace(
        sessions={session_id: chat},
        get_session=lambda sid: chat if sid == session_id else None,
    )
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda _request: "alice")
    monkeypatch.setattr("src.auth_helpers.require_privilege", lambda _request, _priv: "alice")

    router = sr.setup_session_routes(manager, {})
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/api/session/{sid}/save-document"
        and "POST" in getattr(route, "methods", set())
    )

    result = endpoint(SimpleNamespace(state=SimpleNamespace(current_user="alice")), session_id)

    assert result["created"] is True
    assert result["updated"] is False
    db = _TS()
    try:
        document = db.query(Document).filter(Document.id == result["id"]).one()
        version = db.query(DocumentVersion).filter(
            DocumentVersion.document_id == document.id
        ).one()
        assert document.session_id == session_id
        assert document.owner == "alice"
        assert version.version_number == 1
        assert "Hello" in version.content
        assert "Hi there." in version.content
    finally:
        db.close()
