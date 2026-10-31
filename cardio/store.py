import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(run_id,kind));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, at TEXT NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY, accession TEXT UNIQUE NOT NULL, case_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reports(study_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS report_revisions(study_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(study_id,version));
            """)

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=20)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def cases(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM cases ORDER BY id")]

    def case(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT data FROM cases WHERE id=?", (ident,)).fetchone()
            if not row:
                raise KeyError(ident)
            return json.loads(row[0])

    def put_case(self, case):
        with self.connect() as db:
            db.execute("INSERT INTO cases VALUES (?,?)", (case["id"], canonical(case)))

    def run(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (ident,)).fetchone()
            if not row:
                raise KeyError(ident)
            return json.loads(row[0])

    def latest(self, case_id):
        with self.connect() as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs WHERE case_id=?", (case_id,))]
            return max(rows, key=lambda r: r["created_at"])["id"] if rows else None

    def recover_interrupted(self):
        with self.connect() as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs")]
            for run in rows:
                if run["status"] in {"queued", "running"}:
                    run["status"] = "blocked"
                    run["error"] = "上次程序中斷，保留既有證據；請建立新一筆工作。"
                    self.save_run(run, "process_interrupted", db=db)

    def save_run(self, run, kind, detail=None, db=None):
        if db is None:
            with self.connect() as conn:
                return self.save_run(run, kind, detail, conn)
        run["updated_at"] = now()
        db.execute("INSERT INTO runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (run["id"], run["case_id"], canonical(run)))
        self.record_event(run["id"], kind, detail or {}, db)

    def record_event(self, scope, kind, detail=None, db=None):
        if db is None:
            with self.connect() as conn:
                return self.record_event(scope, kind, detail, conn)
        row = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = row[0] if row else "0" * 64
        event = {"at": now(), "run_id": scope, "kind": kind, "data": detail or {}, "previous": previous}
        digest = hashlib.sha256(canonical(event).encode()).hexdigest()
        db.execute("INSERT INTO events(at,run_id,kind,data,previous,hash) VALUES (?,?,?,?,?,?)", (event["at"], scope, kind, canonical(event["data"]), previous, digest))

    def events(self, run_id):
        with self.connect() as db:
            return [dict(r) | {"data": json.loads(r["data"])} for r in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))]

    def verify(self):
        previous = "0" * 64
        with self.connect() as db:
            for r in db.execute("SELECT * FROM events ORDER BY seq"):
                event = {k: r[k] for k in ("at", "run_id", "kind", "previous")}
                event["data"] = json.loads(r["data"])
                digest = hashlib.sha256(canonical(event).encode()).hexdigest()
                if r["previous"] != previous or digest != r["hash"]:
                    return {"valid": False, "first_invalid_seq": r["seq"]}
                previous = r["hash"]
        return {"valid": True, "head": previous, "scope": "local hash chain; not externally anchored"}

    def tasks(self, run_id):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM tasks WHERE run_id=? ORDER BY kind", (run_id,))]
