
from sqlalchemy import text
from app.database.connection import engine

with engine.connect() as connection:

    result = connection.execute(text("""
        SELECT
            current_database(),
            current_user,
            inet_server_addr(),
            inet_server_port()
    """))

    print("Connection details:", result.fetchone())

    result = connection.execute(text("""
        SELECT extname, extversion
        FROM pg_extension
        WHERE extname = 'vector'
    """))

    print("Extension details:", result.fetchall())
