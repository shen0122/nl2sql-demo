import os, re, sqlite3
from openai import OpenAI

DB = "demo.db"
ALLOWED = {"emp"}  # ponytail: only real tables go here; reads of anything else are refused

con = sqlite3.connect(DB)
con.execute("CREATE TABLE IF NOT EXISTS emp(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary REAL)")
con.executemany("INSERT OR IGNORE INTO emp VALUES(?,?,?,?)",
                [(1, "Alice", "Eng", 100), (2, "Bob", "Eng", 200), (3, "Caro", "Sales", 150)])
con.commit()

def _auth(action, a, b, *_):
    if action == sqlite3.SQLITE_READ:
        if a in ALLOWED:
            return sqlite3.SQLITE_OK
        raise RuntimeError(f"refused: access to {a} is prohibited")
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE):
        raise RuntimeError("refused: SELECT only")
    return sqlite3.SQLITE_OK

con.set_authorizer(_auth)  # guardrail: SQLite compiles the statement and tells us what it reads

client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=os.environ.get("OPENAI_API_KEY", "no-key"))

def ask(q):
    r = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system",
                   "content": "Answer only SQL for table emp(id, name, dept, salary)."},
                  {"role": "user", "content": q}])
    m = re.search(r"(?is)^\s*select.*?;", r.choices[0].message.content)
    if not m:
        return "no SQL found in model reply: " + r.choices[0].message.content[:80]
    return con.execute(m.group(0)).fetchall()

if __name__ == "__main__":
    while True:
        print(ask(input("Ask: ")))