import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://zuche:zuche@localhost:5432/zuche_radar_test",
)


def _ensure_default_test_database() -> None:
    if "TEST_DATABASE_URL" in os.environ:
        return
    admin = create_engine("postgresql+psycopg://zuche:zuche@localhost:5432/postgres",
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'zuche_radar_test'"))
        if not exists:
            connection.exec_driver_sql("CREATE DATABASE zuche_radar_test")
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_default_test_database()
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session: Session = factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def clean_test_database(engine):
    """每个测试后清理专用测试库，避免已提交的 API 数据串到后续测试。"""

    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
