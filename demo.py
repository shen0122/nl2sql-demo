"""
Minimal NL2SQL demo: SQLite + OpenAI-compatible API + guardrail layer.

Scope note: this is a *guardrail* demo, not a product. Its purpose is to pin down
where the validation layers belong and what each one actually buys you. It is
deliberately small -- see "Known limitations" in README.md for what it does NOT do.
"""

import os
import re
import sqlite3

from openai import OpenAI

DB_PATH = os.environ.get("NL2SQL_DB", "demo.db")
MAX_ROWS = int(os.environ.get("NL2SQL_MAX_ROWS", "100"))

# Table whitelist. In production this would be per-role config, not a constant.
ALLOWED_TABLES = {"emp"}

# Statements that must never reach the database, whatever the model returns.
DANGEROUS = re.compile(
    r"(?is)\b(drop|delete|update|insert|alter|attach|detach|pragma|create|replace)\b"
)

# Every operation that mutates the database or its schema.
DENY_ACTIONS = {
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
}


# --------------------------------------------------------------- schema layer
def read_schema(con: sqlite3.Connection) -> str:
    """Read the live schema via PRAGMA instead of hard-coding it into the prompt."""
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return "; ".join(
        f"{t}({', '.join(c[1] for c in con.execute(f'PRAGMA table_info({t})'))})"
        for t in tables
    )


# ----------------------------------------------------------- guardrail layer 1
def extract_sql(text: str) -> str | None:
    """Accept exactly one SELECT statement. Anything else -> refuse."""
    text = re.sub(r"(?is)```[a-z]*", "", text).strip()

    m = re.search(r"(?is)\bselect\b.*?;", text)
    if m:
        sql, rest = m.group(0).strip(), text[m.end():].strip()
        if rest:  # a second statement means we did not understand the output
            return None
    else:
        m = re.search(r"(?is)^select\b.*$", text)
        if not m:
            return None
        sql = m.group(0).strip()

    if DANGEROUS.search(sql):
        return None
    return sql


# ----------------------------------------------------------- guardrail layer 2
def install_authorizer(con: sqlite3.Connection) -> None:
    """Let the engine enforce the whitelist, instead of guessing at SQL with regex.

    A regex over `FROM x` misses `JOIN y`, `FROM x, y` and CTEs. The compile-time
    authorizer sees every table the statement actually touches, so the check does
    not depend on us enumerating SQL syntax.
    """

    def authorizer(action, arg1, arg2, dbname, source):
        if action in DENY_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if table and table not in ALLOWED_TABLES:  # also blocks sqlite_master -> no schema leak
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(authorizer)


# ----------------------------------------------------------- guardrail layer 3
def cap_rows(sql: str) -> str:
    """Enforce MAX_ROWS: clamp an existing LIMIT and append one when absent."""
    m = re.search(r"(?is)\blimit\s+(-?\d+)", sql)
    if m:
        n = int(m.group(1))
        if n >= 0 and n <= MAX_ROWS:
            return sql
        return sql[:m.start(1)] + str(MAX_ROWS) + sql[m.end(1):]
    sql = re.sub(r"(?is)--[^\n]*$", "", sql).rstrip("; \n")  # a trailing comment would swallow the appended LIMIT
    return sql + f" LIMIT {MAX_ROWS};"


# ------------------------------------------------------------------- pipeline
def ask(ro: sqlite3.Connection, client: OpenAI, model: str, schema: str, q: str):
    """Returns (rows, sql) on success, (None, refusal_reason) otherwise."""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": f"Answer only SQL for: {schema}"},
            {"role": "user", "content": q},
        ],
    )
    raw = resp.choices[0].message.content or ""

    sql = extract_sql(raw)
    if sql is None:
        return None, "refused: output was not a single SELECT"

    try:
        sql = cap_rows(sql)
        ro.execute(f"EXPLAIN {sql}")  # compiles the statement, never executes it
        return ro.execute(sql).fetchmany(MAX_ROWS), sql
    except sqlite3.Error as exc:
        return None, f"refused: {type(exc).__name__}: {exc}"


def main() -> None:
    rw = sqlite3.connect(DB_PATH)
    rw.execute(
        "CREATE TABLE IF NOT EXISTS emp("
        "id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary REAL)"
    )
    rw.executemany(
        "INSERT OR IGNORE INTO emp VALUES(?,?,?,?)",
        [(1, "Alice", "Eng", 100), (2, "Bob", "Eng", 200), (3, "Caro", "Sales", 150)],
    )
    rw.commit()
    schema = read_schema(rw)

    # Layer 4: the query path never opens a writable handle.
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    install_authorizer(ro)

    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "no-key"),  # works with Ollama / LM Studio
    )
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    while True:
        try:
            q = input("Ask: ")
        except (EOFError, KeyboardInterrupt):
            break
        if not q.strip():
            continue
        rows, info = ask(ro, client, model, schema, q)
        print(info if rows is None else rows)


if __name__ == "__main__":
    main()
