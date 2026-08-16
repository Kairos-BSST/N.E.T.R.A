from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from config import Config

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> str:
    return Config.DATABASE_PATH


@contextmanager
def connection():
    os.makedirs(os.path.dirname(db_path()) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('administrator','operator')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                user_id INTEGER,
                source TEXT,
                stream_url TEXT,
                local_path TEXT,
                original_name TEXT,
                status TEXT,
                message TEXT,
                error TEXT,
                queued_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT,
                progress REAL DEFAULT 0,
                frames_processed INTEGER DEFAULT 0,
                total_frames INTEGER,
                result_json TEXT,
                video_info_json TEXT,
                extra_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_user ON analysis_jobs(user_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_queued ON analysis_jobs(queued_at);
            CREATE TABLE IF NOT EXISTS analysis_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT,
                label TEXT,
                plate_number TEXT,
                wall_clock_time TEXT,
                video_time_seconds REAL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, event_id),
                FOREIGN KEY(job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_events_job ON analysis_events(job_id);
            CREATE INDEX IF NOT EXISTS idx_events_type ON analysis_events(event_type);
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_id TEXT NOT NULL UNIQUE,
                job_id TEXT NOT NULL,
                user_id INTEGER,
                evidence_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_job ON evidence(job_id);
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                job_id TEXT,
                resource_type TEXT,
                resource_id TEXT,
                details_json TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
            CREATE TABLE IF NOT EXISTS poi_persons (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                notes TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS poi_faces (
                id TEXT PRIMARY KEY,
                poi_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT,
                embedding BLOB NOT NULL,
                detect_score REAL,
                bbox_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(poi_id) REFERENCES poi_persons(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_poi_faces_poi ON poi_faces(poi_id);
            """
        )
        _ensure_column(conn, "analysis_jobs", "local_path", "TEXT")
        _ensure_column(conn, "analysis_jobs", "video_info_json", "TEXT")
        _ensure_default_user(conn, Config.ADMIN_USERNAME, Config.ADMIN_PASSWORD, "administrator")
        _ensure_default_user(conn, Config.OPERATOR_USERNAME, Config.OPERATOR_PASSWORD, "operator")


def _ensure_default_user(conn: sqlite3.Connection, username: str, password: str, role: str) -> None:
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
            (username, hash_password(password), role, _now()),
        )


def create_user(username: str, password: str, role: str = "operator") -> Dict[str, Any]:
    username = username.strip()
    if not username:
        raise ValueError("Username is required")
    if role not in {"operator", "administrator"}:
        raise ValueError("Invalid role")
    with connection() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                (username, hash_password(password), role, _now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists") from exc
        row = conn.execute(
            "SELECT id,username,role,is_active,created_at FROM users WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def hash_password(password: str, iterations: int = 310_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_session(user_id: int, ttl_seconds: int) -> str:
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires = now.timestamp() + ttl_seconds
    expires_iso = datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()
    with connection() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (token_hash, user_id, now.isoformat(), expires_iso),
        )
    return raw


def get_user_by_session(raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with connection() as conn:
        row = conn.execute(
            """
            SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1
            """,
            (token_hash, _now()),
        ).fetchone()
        return dict(row) if row else None


def session_user(raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
    return get_user_by_session(raw_token)


def delete_session(raw_token: Optional[str]) -> None:
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def record_job(job: Dict[str, Any]) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO analysis_jobs(
              job_id,user_id,source,stream_url,local_path,original_name,status,message,error,
              queued_at,started_at,completed_at,updated_at,progress,frames_processed,
              total_frames,result_json,video_info_json,extra_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
              user_id=excluded.user_id,source=excluded.source,stream_url=excluded.stream_url,local_path=excluded.local_path,
              original_name=excluded.original_name,status=excluded.status,message=excluded.message,
              error=excluded.error,queued_at=excluded.queued_at,started_at=excluded.started_at,
              completed_at=excluded.completed_at,updated_at=excluded.updated_at,progress=excluded.progress,
              frames_processed=excluded.frames_processed,total_frames=excluded.total_frames,
              result_json=excluded.result_json,extra_json=excluded.extra_json
            """,
            (
                job.get("job_id"), job.get("user_id"), job.get("source"), job.get("stream_url"), job.get("local_path"),
                job.get("original_name"), job.get("status"), job.get("message"), job.get("error"),
                job.get("queued_at"), job.get("started_at"), job.get("completed_at"), job.get("updated_at"),
                job.get("progress", 0.0), job.get("frames_processed", 0), job.get("total_frames"),
                json.dumps(job.get("result"), default=str) if job.get("result") is not None else None,
                json.dumps(job.get("video_info"), default=str) if job.get("video_info") is not None else None,
                json.dumps(job.get("extra") or {}, default=str), _now(),
            ),
        )


def update_job(job: Dict[str, Any]) -> None:
    record_job(job)


def _job_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json")) if d.get("result_json") else None
    d["video_info"] = json.loads(d.pop("video_info_json")) if d.get("video_info_json") else None
    d["extra"] = json.loads(d.pop("extra_json")) if d.get("extra_json") else {}
    return d


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = _job_from_row(row)
        events = conn.execute("SELECT event_json FROM analysis_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        job["events"] = [json.loads(r[0]) for r in events]
        return job


def list_jobs(limit: int = 100) -> list[Dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM analysis_jobs ORDER BY queued_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        jobs = []
        for row in rows:
            job = _job_from_row(row)
            events = conn.execute("SELECT event_json FROM analysis_events WHERE job_id=? ORDER BY id", (job["job_id"],)).fetchall()
            job["events"] = [json.loads(r[0]) for r in events]
            jobs.append(job)
        return jobs


def add_event(job_id: str, event: Dict[str, Any]) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO analysis_events(job_id,event_id,event_type,label,plate_number,wall_clock_time,video_time_seconds,event_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id, event.get("event_id"), event.get("type"), event.get("label"), event.get("plate_number"),
                event.get("wall_clock_time"), event.get("video_time_seconds"), json.dumps(event, default=str), _now(),
            ),
        )



def hash_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def add_evidence(job_id: str, user_id: Optional[int], evidence_type: str, file_path: str, file_name: str, sha256: str) -> str:
    evidence_id = secrets.token_hex(12)
    with connection() as conn:
        conn.execute(
            "INSERT INTO evidence(evidence_id,job_id,user_id,evidence_type,file_path,file_name,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (evidence_id, job_id, user_id, evidence_type, file_path, file_name, sha256, _now()),
        )
    return evidence_id


def record_evidence(job_id: str, user_id: Optional[int], evidence_type: str, file_path: str) -> Optional[str]:
    if not os.path.isfile(file_path):
        return None
    return add_evidence(job_id, user_id, evidence_type, file_path, os.path.basename(file_path), hash_file(file_path))


def evidence_for_job(job_id: str) -> list[Dict[str, Any]]:
    with connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM evidence WHERE job_id=? ORDER BY created_at", (job_id,)).fetchall()]


def record_audit(user_id: Optional[int], action: str, job_id: Optional[str] = None, resource_type: Optional[str] = None, resource_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO audit_logs(user_id,action,job_id,resource_type,resource_id,details_json,timestamp) VALUES(?,?,?,?,?,?,?)",
            (user_id, action, job_id, resource_type, resource_id, json.dumps(details or {}, default=str), _now()),
        )


def history_jobs(user_id: Optional[int] = None, is_admin: bool = False, search: str = "", source: str = "", status: str = "", event_type: str = "", date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 25) -> Dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if not is_admin:
        clauses.append("j.user_id = ?")
        params.append(user_id)
    elif user_id:
        clauses.append("j.user_id = ?")
        params.append(user_id)
    if source:
        clauses.append("j.source = ?")
        params.append(source)
    if status:
        clauses.append("j.status = ?")
        params.append(status)
    if date_from:
        clauses.append("j.queued_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("j.queued_at <= ?")
        params.append(date_to)
    if search:
        like = f"%{search}%"
        clauses.append("(j.job_id LIKE ? OR j.original_name LIKE ? OR j.source LIKE ? OR u.username LIKE ? OR EXISTS (SELECT 1 FROM analysis_events se WHERE se.job_id=j.job_id AND (se.label LIKE ? OR se.plate_number LIKE ? OR se.event_type LIKE ?)))")
        params.extend([like, like, like, like, like, like, like])
    if event_type:
        clauses.append("EXISTS (SELECT 1 FROM analysis_events et WHERE et.job_id=j.job_id AND et.event_type=?)")
        params.append(event_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM analysis_jobs j LEFT JOIN users u ON u.id=j.user_id{where}", params).fetchone()[0]
        offset = max(0, page - 1) * page_size
        rows = conn.execute(f"SELECT j.*, u.username, u.role FROM analysis_jobs j LEFT JOIN users u ON u.id=j.user_id{where} ORDER BY j.queued_at DESC LIMIT ? OFFSET ?", params + [page_size, offset]).fetchall()
        jobs = []
        for row in rows:
            job = _job_from_row(row)
            job["username"] = row["username"]
            job["role"] = row["role"]
            ev_rows = conn.execute("SELECT event_json FROM analysis_events WHERE job_id=? ORDER BY id", (job["job_id"],)).fetchall()
            job["events"] = [json.loads(r[0]) for r in ev_rows]
            job["report_downloaded"] = bool(conn.execute(
                "SELECT 1 FROM audit_logs WHERE job_id=? AND action='REPORT_DOWNLOADED' LIMIT 1",
                (job["job_id"],),
            ).fetchone())
            jobs.append(job)
    return {"jobs": jobs, "total": total, "page": page, "page_size": page_size}


def audit_history(search: str = "", username: str = "", action: str = "", date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 50, allowed_actions: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if search:
        like = f"%{search}%"
        clauses.append("(a.action LIKE ? OR a.job_id LIKE ? OR a.resource_id LIKE ? OR a.details_json LIKE ? OR u.username LIKE ?)")
        params.extend([like] * 5)
    if username:
        clauses.append("u.username = ?")
        params.append(username)
    if action:
        clauses.append("a.action = ?")
        params.append(action)
    if allowed_actions:
        marks = ",".join("?" for _ in allowed_actions)
        clauses.append(f"a.action IN ({marks})")
        params.extend(list(allowed_actions))
    if date_from:
        clauses.append("a.timestamp >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("a.timestamp <= ?")
        params.append(date_to)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id{where}", params).fetchone()[0]
        offset = max(0, page - 1) * page_size
        rows = conn.execute(f"SELECT a.*,u.username,u.role,j.original_name,j.source FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id LEFT JOIN analysis_jobs j ON j.job_id=a.job_id{where} ORDER BY a.timestamp DESC LIMIT ? OFFSET ?", params + [page_size, offset]).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json") or "{}")
            items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def users_for_filter() -> list[Dict[str, Any]]:
    with connection() as conn:
        return [dict(r) for r in conn.execute("SELECT id,username,role,is_active,created_at FROM users ORDER BY username").fetchall()]


def _poi_face_public(row, include_embedding: bool = False) -> Dict[str, Any]:
    data = dict(row)
    face = {
        "face_id": data["id"],
        "poi_id": data["poi_id"],
        "file_name": data.get("file_name"),
        "image_url": f"/poi/{data['poi_id']}/faces/{data['id']}/image",
        "detect_score": data.get("detect_score"),
        "bbox": json.loads(data["bbox_json"]) if data.get("bbox_json") else None,
        "created_at": data.get("created_at"),
    }
    if include_embedding:
        face["embedding"] = data.get("embedding")
    return face


def list_pois(enabled_only: bool = False) -> List[Dict[str, Any]]:
    with connection() as conn:
        if enabled_only:
            persons = conn.execute(
                "SELECT * FROM poi_persons WHERE enabled=1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            persons = conn.execute(
                "SELECT * FROM poi_persons ORDER BY created_at DESC"
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for person in persons:
            faces = conn.execute(
                "SELECT id,poi_id,file_name,detect_score,bbox_json,created_at FROM poi_faces WHERE poi_id=? ORDER BY created_at",
                (person["id"],),
            ).fetchall()
            item = dict(person)
            item["enabled"] = bool(item.get("enabled"))
            item["faces"] = [_poi_face_public(f) for f in faces]
            item["face_count"] = len(item["faces"])
            out.append(item)
        return out


def get_poi(poi_id: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        person = conn.execute("SELECT * FROM poi_persons WHERE id=?", (poi_id,)).fetchone()
        if person is None:
            return None
        faces = conn.execute(
            "SELECT id,poi_id,file_name,detect_score,bbox_json,created_at FROM poi_faces WHERE poi_id=? ORDER BY created_at",
            (poi_id,),
        ).fetchall()
        item = dict(person)
        item["enabled"] = bool(item.get("enabled"))
        item["faces"] = [_poi_face_public(f) for f in faces]
        item["face_count"] = len(item["faces"])
        return item


def get_poi_face(poi_id: str, face_id: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM poi_faces WHERE id=? AND poi_id=?",
            (face_id, poi_id),
        ).fetchone()
        return dict(row) if row else None


def create_poi(*, poi_id: str, name: str, notes: str = "", created_by: Optional[int] = None, enabled: bool = True) -> Dict[str, Any]:
    now = _now()
    with connection() as conn:
        conn.execute(
            "INSERT INTO poi_persons(id,name,notes,enabled,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (poi_id, name.strip(), notes or "", 1 if enabled else 0, created_by, now, now),
        )
    return get_poi(poi_id) or {"id": poi_id, "name": name}


def add_poi_face(*, face_id: str, poi_id: str, file_path: str, file_name: str, embedding: bytes, detect_score: Optional[float] = None, bbox: Optional[list] = None) -> Dict[str, Any]:
    with connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM poi_faces WHERE poi_id=?", (poi_id,)).fetchone()[0]
        if count >= 2:
            raise ValueError("Each person-of-interest can store at most 2 face images.")
        conn.execute(
            "INSERT INTO poi_faces(id,poi_id,file_path,file_name,embedding,detect_score,bbox_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (face_id, poi_id, file_path, file_name, embedding, detect_score, json.dumps(bbox) if bbox is not None else None, _now()),
        )
        conn.execute("UPDATE poi_persons SET updated_at=? WHERE id=?", (_now(), poi_id))
    face = get_poi_face(poi_id, face_id)
    return _poi_face_public(face) if face else {"face_id": face_id, "poi_id": poi_id}


def delete_poi(poi_id: str) -> bool:
    with connection() as conn:
        faces = conn.execute("SELECT file_path FROM poi_faces WHERE poi_id=?", (poi_id,)).fetchall()
        paths = [r["file_path"] for r in faces]
        cur = conn.execute("DELETE FROM poi_persons WHERE id=?", (poi_id,))
        deleted = cur.rowcount > 0
    for path in paths:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    folder = os.path.join(Config.POI_GALLERY_DIR, poi_id)
    try:
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    except OSError:
        pass
    return deleted


def list_poi_embeddings() -> List[Dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT f.id AS face_id, f.poi_id, f.embedding, p.name
            FROM poi_faces f
            JOIN poi_persons p ON p.id = f.poi_id
            WHERE p.enabled = 1
            """
        ).fetchall()
        return [
            {"face_id": r["face_id"], "poi_id": r["poi_id"], "name": r["name"], "embedding": r["embedding"]}
            for r in rows
        ]
