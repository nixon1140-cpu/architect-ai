"""pytest共通フィクスチャ。

DBはSQLiteのインメモリDBに差し替え、本番のPostgreSQLには一切接続しない
（仕様書 Step6 要件4）。app.main を import する前に DATABASE_URL を
SQLiteへ向けておくことで、FastAPIのlifespanが行う create_all も
PostgreSQLへ接続しないようにする。
"""

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# app.main を import するより前に設定する必要がある
# （main.py はモジュール読み込み時に os.environ から DATABASE_URL を読むため）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OLLAMA_BASE_URL", "http://ollama-not-used-in-tests:11434")
os.environ.setdefault("OLLAMA_MODEL", "gemma4")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base


@pytest.fixture()
def test_engine():
    """テストごとに新しいSQLiteインメモリDBを用意する。

    StaticPoolで単一コネクションを使い回すことで、
    :memory: DBが複数コネクション間でリセットされてしまう問題を避ける。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(test_engine):
    """DB依存をテスト用SQLiteエンジンへ差し替え済みのTestClient。"""
    from fastapi.testclient import TestClient

    from app.main import app, get_db

    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
