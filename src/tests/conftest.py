import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import Base

@pytest.fixture(scope="session")
def engine():
    # STD-SEC-1: Database credentials must not be hardcoded.
    # Using in-memory SQLite for tests to ensure no secrets are required.
    return create_engine("sqlite:///:memory:", echo=False)

@pytest.fixture(scope="session")
def tables(engine):
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture
def db_session(engine, tables):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()
