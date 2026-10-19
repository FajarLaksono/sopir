from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.settings import Settings

engine = create_engine(Settings.sync_database_url, pool_pre_ping=True)
session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = session_local()
    try:
        yield db
    finally:
        db.close()