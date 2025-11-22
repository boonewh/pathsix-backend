from app.database import Base, engine

# Drop all tables and recreate them from the current model definitions
if __name__ == "__main__":
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("✅ Tables reset using pure SQLAlchemy.")
