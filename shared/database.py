from sqlmodel import SQLModel, create_engine, Session
from shared.config import get

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        from pathlib import Path
        db_path = Path(get("database.path", "wdbx.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    return _engine

def create_db_and_tables():
    SQLModel.metadata.create_all(get_engine())

def get_session():
    with Session(get_engine()) as session:
        yield session
