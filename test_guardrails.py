"""Offline guardrail tests. No network, no API key -- the model is stubbed.

    python test_guardrails.py

Each layer is tested for what it should *block*, because a guardrail that only
passes the happy path is not a guardrail.
"""

import os
import sqlite3
import sys
import tempfile
import types

# --- stub `openai` so the tests need neither the dependency nor a network ----
_pkg = types.ModuleType("openai")


class _OpenAI:
    def __init__(self, **kw):
        pass


_pkg.OpenAI = _OpenAI
sys.modules["openai"] = _pkg

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import demo  # noqa: E402

PASSED, FAILED = [], []


def check(label, got, want):
    (PASSED if got == want else FAILED).append(label)
    if got != want:
        print(f"FAIL  {label}\n        got ={got!r}\n        want={want!r}")


def try_exec(con, sql):
    """'OK', or the name of the sqlite exception raised."""
    try:
        con.execute(sql)
        return "OK"
    except sqlite3.Error as exc:
        return type(exc).__name__


class FakeClient:
    """Stands in for OpenAI(); returns whatever the model 'said'."""

    def __init__(self, content):
        def create(**kw):
            msg = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))


# --------------------------------------------------------- layer 1: extract_sql
print("layer 1  extract_sql")
check("plain select", demo.extract_sql("SELECT * FROM emp;"), "SELECT * FROM emp;")
check("no trailing semicolon", demo.extract_sql("SELECT * FROM emp"), "SELECT * FROM emp")
check("markdown fenced", demo.extract_sql("```sql\nSELECT name FROM emp;\n```"),
      "SELECT name FROM emp;")
check("chatty prefix", demo.extract_sql("Sure! Here: SELECT * FROM emp;"),
      "SELECT * FROM emp;")
check("multi-statement injection", demo.extract_sql("SELECT 1; DROP TABLE emp;"), None)
check("delete only", demo.extract_sql("DELETE FROM emp;"), None)
check("drop after select", demo.extract_sql("SELECT x FROM emp; DROP TABLE emp;"), None)
check("natural language", demo.extract_sql("抱歉，我无法回答这个问题。"), None)
check("empty", demo.extract_sql(""), None)

# ----------------------------------------------------------- layer 3: cap_rows
print("layer 3  cap_rows")
check("auto limit", demo.cap_rows("SELECT * FROM emp;"), "SELECT * FROM emp LIMIT 100;")
check("existing limit kept", demo.cap_rows("SELECT * FROM emp LIMIT 5;"),
      "SELECT * FROM emp LIMIT 5;")

# -------------------------------------------------------------- test database
db = os.path.join(tempfile.gettempdir(), "nl2sql_guardrail_test.db")
if os.path.exists(db):
    os.remove(db)
demo.DB_PATH = db

rw = sqlite3.connect(db)
rw.execute("CREATE TABLE emp(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary REAL)")
rw.execute("CREATE TABLE secrets(id INTEGER PRIMARY KEY, token TEXT)")
rw.executemany("INSERT OR IGNORE INTO emp VALUES(?,?,?,?)",
               [(1, "Alice", "Eng", 100), (2, "Bob", "Eng", 200), (3, "Caro", "Sales", 150)])
rw.commit()

print("schema layer  read_schema")
check("schema read from PRAGMA", demo.read_schema(rw),
      "emp(id, name, dept, salary); secrets(id, token)")

# ------------------------------------------------- layer 2: engine authorizer
print("layer 2  install_authorizer")
ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
demo.install_authorizer(ro)

for label, sql, want in [
    ("plain select allowed",    "SELECT * FROM emp",                                      "OK"),
    ("join to other table",     "SELECT * FROM emp JOIN secrets ON 1",                    "DatabaseError"),
    ("comma join",              "SELECT * FROM emp, secrets",                             "DatabaseError"),
    ("subquery",                "SELECT * FROM emp WHERE id IN (SELECT id FROM secrets)", "DatabaseError"),
    ("cte",                     "WITH s AS (SELECT * FROM secrets) SELECT * FROM s",       "DatabaseError"),
    ("union",                   "SELECT name FROM emp UNION SELECT token FROM secrets",   "DatabaseError"),
    ("write via update",        "UPDATE emp SET salary = 0",                              "DatabaseError"),
    ("write via delete",        "DELETE FROM emp",                                        "DatabaseError"),
    ("schema change",           "DROP TABLE emp",                                         "DatabaseError"),
]:
    check(label, try_exec(ro, sql), want)

# -------------------------------------------------------- layer 4: read-only handle
print("layer 4  read-only connection")
check("read-only query works",
      ro.execute(demo.cap_rows("SELECT name FROM emp ORDER BY salary DESC;")).fetchall(),
      [("Bob",), ("Caro",), ("Alice",)])

bare_ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # mode=ro on its own
check("mode=ro alone blocks writes", try_exec(bare_ro, "INSERT INTO emp VALUES(9,'X','Y',9)"),
      "OperationalError")

# ------------------------------------------------------------ end-to-end pipeline
print("pipeline  ask()")
schema = demo.read_schema(rw)
for label, model_said, want_rows in [
    ("normal query",   "SELECT name FROM emp;",                                            "rows"),
    ("destructive",    "DROP TABLE emp;",                                                  "refused"),
    ("read other tbl", "SELECT * FROM secrets;",                                           "refused"),
    ("join other tbl", "SELECT e.name, s.token FROM emp e JOIN secrets s ON 1;",           "refused"),
    ("no sql at all",  "抱歉，我无法回答这个问题。",                                            "refused"),
]:
    rows, info = demo.ask(ro, FakeClient(model_said), "stub", schema, "q")
    got = "rows" if rows is not None else "refused"
    check(label, got, want_rows)
    print(f"        {label:14s} -> {info!r}")

bare_ro.close()
ro.close()
rw.close()

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    print("failing: " + ", ".join(FAILED))
sys.exit(1 if FAILED else 0)
