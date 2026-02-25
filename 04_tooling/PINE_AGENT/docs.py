import sqlite3
from pathlib import Path

class Docs:
    def __init__(self, db_path="docs.db"):
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

    def list(self):
        """List all documentation entry names."""
        rows = self.conn.execute("SELECT name FROM items ORDER BY name;").fetchall()
        return [r["name"] for r in rows]

    def get(self, name):
        """Get documentation entry by exact name."""
        row = self.conn.execute(
            "SELECT * FROM items WHERE name = ?;",
            (name,)
        ).fetchone()
        return dict(row) if row else None

    def search(self, text):
        """Search description, remarks, and code text."""
        q = f"%{text}%"
        cur = self.conn.execute("""
            SELECT * FROM items
            WHERE name LIKE ?
               OR description LIKE ?
               OR remarks LIKE ?
               OR code LIKE ?
            ORDER BY name;
        """, (q, q, q, q))

        return [dict(r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()
