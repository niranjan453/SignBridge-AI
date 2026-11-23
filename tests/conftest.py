import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import Settings, get_settings
from app.main import create_app

SECRET = "ephemeral-test-key-used-only-by-pytest"


@pytest.fixture
def database_url(tmp_path):
    postgres_url = os.getenv("TEST_DATABASE_URL")
    if not postgres_url:
        yield f"sqlite:///{tmp_path / 'test.sqlite'}"
        return

    engine = create_engine(postgres_url)
    schema = f"signbridge_test_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        url = make_url(postgres_url).update_query_dict({"options": f"-csearch_path={schema}"})
        try:
            yield url.render_as_string(hide_password=False)
        finally:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


@pytest.fixture
def create_client(monkeypatch, database_url):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()

    def factory(**overrides):
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        settings = Settings(database_url=database_url, jwt_secret=SECRET, media_max_bytes=1024, **overrides)
        app = create_app(settings)
        engine = app.state.sessions.kw["bind"]
        if engine.dialect.name == "sqlite":
            @event.listens_for(engine, "connect")
            def enable_fk(dbapi_connection, _):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return TestClient(app), config

    yield factory
    get_settings.cache_clear()


@pytest.fixture
def client(create_client):
    client, _ = create_client()
    with client:
        yield client
