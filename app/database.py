from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy import create_engine
from app.config import SQLALCHEMY_DATABASE_URI
import os

engine = create_engine(SQLALCHEMY_DATABASE_URI, echo=False, future=True)

SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()