import os, re, sqlite3
from openai import OpenAI

DB = "demo.db"
con = sqlite3.connect(DB)
con.execute("CREATE TABLE IF NOT EXISTS emp(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary REAL)")
con.executemany("INSERT OR IGNORE INTO emp VALUES(?,?,?,?)",
                [(1, "Alice", "Eng", 100), (2, "Bob", "Eng", 200), (3, "Caro", "Sales", 150)])
con.commit()

client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=os.environ.get("OPENAI_API_KEY", "no-key"))  # works with Ollama/LM Studio etc.

def ask(q):
    r = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system",
                   "content": "Answer only SQL for table emp(id, name, dept, salary)."},
                  {"role": "user", "content": q}])
    sql = re.search(r"(?is)^\s*select.*?;", r.choices[0].message.content).group(0)  # select-only
    con.execute(f"EXPLAIN {sql}")  # guardrail: SQLite parses it, never runs side effects
    return con.execute(sql).fetchall()

if __name__ == "__main__":
    while True:
        print(ask(input("Ask: ")))