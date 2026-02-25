import re
import sqlite3
from pathlib import Path

# -----------------------------------------------
# PARSING
# -----------------------------------------------

def split_sections(text: str):
    """Split on lines that are exactly ---."""
    raw = text.split('---')
    return [s.strip() for s in raw if s.strip()]

def parse_section(block: str):
    """
    Parse a section with guaranteed structure:
    ## <name>
    description
    ### Remarks (optional)
    remarks text
    ### Code Example (optional)
    ``` code ```
    """
    lines = block.splitlines()

    # ----------------------------
    # Extract name from ## line
    # ----------------------------
    name_line = next((l for l in lines if l.startswith("## ")), None)
    if not name_line:
        return None

    name = name_line[3:].strip()

    # Everything after the ## line:
    after = block.split(name_line, 1)[1].strip()

    # ----------------------------
    # Split major subsections
    # ----------------------------
    # Possible patterns:
    #   ### Remarks
    #   ### Code Example
    # Always in that order if present.
    parts = re.split(r'\n### ', after)

    # parts[0] = description
    description = parts[0].strip()

    remarks = None
    code = None

    for p in parts[1:]:
        header, *content_lines = p.split("\n")
        header = header.strip()

        content = "\n".join(content_lines).strip()

        if header == "Remarks":
            remarks = content

        elif header == "Code Example":
            # extract the single code block
            m = re.search(r"```(.*?)```", content, flags=re.DOTALL)
            if m:
                code = m.group(1).strip()

    return {
        "name": name,
        "description": description,
        "remarks": remarks,
        "code": code,
        "raw": block
    }


# -----------------------------------------------
# SQL SETUP
# -----------------------------------------------

def init_db(db_path="docs.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        remarks TEXT,
        code TEXT,
        raw_text TEXT
    )
    """)

    conn.commit()
    return conn

# -----------------------------------------------
# INGEST PIPELINE
# -----------------------------------------------

def ingest_file(path, db_path="docs.db"):
    text = Path(path).read_text(encoding='utf-8')

    conn = init_db(db_path)
    cur = conn.cursor()

    sections = split_sections(text)

    for block in sections:
        parsed = parse_section(block)
        if parsed:
            cur.execute("""
                INSERT INTO items (name, description, remarks, code, raw_text)
                VALUES (?, ?, ?, ?, ?)
            """, (parsed['name'], parsed['description'], parsed['remarks'], parsed['code'], parsed['raw']))

    conn.commit()
    conn.close()

if __name__== "__main__":
    ingest_file('pine_refrence.txt')
    print("DONE")