"""Seed a new attempt's state.db with every attempt-1 row marked 'copy from the previous attempt' (up=2); rows re-processed later overwrite it."""
import sqlite3, sys
src, dst = sys.argv[1:3]
a = sqlite3.connect(src); b = sqlite3.connect(dst)
b.executescript('''create table if not exists files(rel text primary key, out text, md5 text, size int, status text, note text, up int default 0, prev_out text);
create table if not exists blobs(md5 text primary key, size int, local int default 1, key text);''')
b.executemany("insert or replace into files(rel,out,md5,size,status,note,up,prev_out) values(?,?,?,?,?,?,2,?)",
              [(r, o, m, s, st, n, o) for r, o, m, s, st, n in a.execute("select rel,out,md5,size,status,note from files where status='ok'")])
b.commit(); print(b.execute('select count(*) from files').fetchone()[0], 'rows seeded')
