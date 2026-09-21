import sqlite3
import demo

con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE emp(id, name, dept, salary)")
con.execute("CREATE TABLE secrets(token)")
con.set_authorizer(demo._auth)

for bad in ["SELECT * FROM secrets",
            "SELECT * FROM emp JOIN secrets ON 1",
            "SELECT * FROM emp, secrets",
            "SELECT * FROM (SELECT * FROM secrets)",
            "WITH s AS (SELECT * FROM secrets) SELECT * FROM s",
            "SELECT * FROM emp UNION SELECT * FROM secrets",
            "DELETE FROM emp",
            "INSERT INTO emp VALUES(1,'x','y',0)",
            "DROP TABLE emp",
            "CREATE TABLE hack(x)"]:
    try:
        con.execute(bad)
        raise SystemExit(f"FAIL: '{bad}' not blocked")
    except Exception:
        pass

assert con.execute("SELECT * FROM emp").fetchall() == []
print("PASS: 10 bypasses blocked, allowed SELECT runs")