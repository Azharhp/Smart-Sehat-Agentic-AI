import sqlite3

conn = sqlite3.connect("smartsehat.db")
conn.row_factory = sqlite3.Row
print("USERS:")
for r in conn.execute("SELECT user_id,name,role,bhu_id FROM users ORDER BY user_id"):
    print(" ", tuple(r))
print("DHO user_facilities:",
      [tuple(x) for x in conn.execute(
          "SELECT user_id,bhu_id FROM user_facilities WHERE user_id='DHO-0001'")])
print("COUNTS cases/media/events:",
      tuple(conn.execute("SELECT (SELECT COUNT(*) FROM cases),"
                         "(SELECT COUNT(*) FROM media_artifacts),"
                         "(SELECT COUNT(*) FROM events)").fetchone()))
conn.close()
