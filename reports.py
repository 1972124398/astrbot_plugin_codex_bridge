"""Durable global observations and notification outbox (at-least-once delivery)."""

import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


def progress_view(detail):
    thread = detail.get("thread", {})
    turns = detail.get("turns", [])
    # Desktop read_thread returns newest turn first. Forward public commentary
    # and results only, never reasoning, command output, or tool arguments.
    turn = turns[0] if turns else {}
    messages = [
        item.get("text", "")
        for item in turn.get("items", [])
        if item.get("type") == "agentMessage"
        and isinstance(item.get("text"), str) and item["text"].strip()
    ]
    return {
        "status": thread.get("status"),
        "turn_id": turn.get("id"),
        "turn_status": turn.get("status"),
        "error": turn.get("error"),
        "messages": messages[-3:],
    }


def digest(view):
    return hashlib.sha256(
        json.dumps(view, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def observation_status(status):
    # Desktop can load or unload an inactive thread without changing its turn.
    status_type = status.get("type") if isinstance(status, dict) else status
    if status_type in ("idle", "notLoaded"):
        return "idle"
    return status


def is_active(row):
    state = row.get("status")
    return (
        state == "active"
        or isinstance(state, dict)
        and state.get("type") in ("active", "systemError")
    )


def reportable_progress(view):
    """A running status without public text is an observation, not a notice."""
    status = view.get("status")
    status_type = status.get("type") if isinstance(status, dict) else status
    flags = status.get("activeFlags", []) if isinstance(status, dict) else []
    return bool(
        any(isinstance(text, str) and text.strip() for text in view.get("messages", []))
        or view.get("error")
        or view.get("turn_status") in ("completed", "failed", "interrupted")
        or status_type == "systemError"
        or any(flag in ("waitingOnUserInput", "waitingOnApproval") for flag in flags)
    )


class ReportStore:
    def __init__(self, data_dir: Path):
        self.db = sqlite3.connect(data_dir / "reports.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                identity TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, reported REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL,
                fingerprint TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries (
                report_id INTEGER NOT NULL, session TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY(report_id,session));
            CREATE TABLE IF NOT EXISTS thread_metadata(identity TEXT PRIMARY KEY, signature TEXT NOT NULL);
        """)
        self.db.commit()

    def observe(
        self,
        row,
        detail,
        sessions,
        interval,
        *,
        owned=False,
        now=None,
        report_new=False,
    ):
        now = time.time() if now is None else now
        identity = f"{row.get('hostId', 'local')}:{row['id']}"
        view = progress_view(detail)
        status = observation_status(view["status"])
        fingerprint = digest(dict(view, status=status))
        state = [status, view["turn_id"], view["turn_status"], view["error"]]
        old = self.db.execute(
            "SELECT * FROM observations WHERE identity=?", (identity,)
        ).fetchone()
        changed = True
        transition = False
        if old is not None:
            old_status, *old_turn = json.loads(old["status"])
            old_state = [observation_status(old_status), *old_turn]
            transition = state != old_state
            changed = old["fingerprint"] != fingerprint
            if changed and not transition and old_status != status:
                # Older releases hashed the raw Desktop status in saved observations.
                changed = old["fingerprint"] != digest(dict(view, status=old_status))
        # Seed old idle history silently. Active tasks are reported on first sight.
        should_report = (
            not owned
            and changed
            and reportable_progress(view)
            and (
                (old is None and (is_active(row) or report_new))
                or transition
                or (old is not None and (not old["reported"] or now - old["reported"] >= interval))
            )
        )
        reported = now if should_report else (old["reported"] if old else 0)
        # Keep the old fingerprint and its status together while throttling.
        throttled = (
            old is not None and changed and not should_report
            and not owned and reportable_progress(view)
        )
        saved_fingerprint = old["fingerprint"] if throttled else fingerprint
        saved_state = old["status"] if throttled else json.dumps(state, sort_keys=True)
        with self.db:
            self.db.execute(
                """INSERT INTO observations VALUES(?,?,?,?,?)
                ON CONFLICT(identity) DO UPDATE SET fingerprint=excluded.fingerprint,
                status=excluded.status,reported=excluded.reported,updated=excluded.updated""",
                (identity, saved_fingerprint, saved_state, reported, now),
            )
            if should_report:
                title = (
                    row.get("title")
                    or detail.get("thread", {}).get("title")
                    or row["id"]
                )
                payload = dict(
                    kind="task_progress",
                    thread_id=row["id"],
                    host_id=row.get("hostId", "local"),
                    title=title,
                    progress=view,
                    observed_at=now,
                )
                report_id = self.db.execute(
                    "INSERT INTO reports(identity,fingerprint,payload,created) VALUES(?,?,?,?)",
                    (
                        identity,
                        fingerprint,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                ).lastrowid
                for session in dict.fromkeys(sessions):
                    self.db.execute(
                        """UPDATE deliveries SET state='superseded'
                        WHERE session=? AND state='pending' AND report_id IN
                        (SELECT id FROM reports WHERE identity=? AND json_extract(payload,'$.progress.turn_id') IS ?)""",
                        (session, identity, view["turn_id"]),
                    )
                    self.db.execute(
                        "INSERT INTO deliveries(report_id,session) VALUES(?,?)",
                        (report_id, session),
                    )
        return should_report

    def add_notice(self, session, message):
        payload = {
            "kind": "codex_notice",
            "content": message,
            "observed_at": time.time(),
        }
        with self.db:
            report_id = self.db.execute(
                "INSERT INTO reports(identity,fingerprint,payload,created) VALUES(?,?,?,?)",
                (
                    "notice:" + uuid.uuid4().hex,
                    digest(payload),
                    json.dumps(payload, ensure_ascii=False),
                    time.time(),
                ),
            ).lastrowid
            self.db.execute(
                "INSERT INTO deliveries(report_id,session) VALUES(?,?)",
                (report_id, session),
            )
        return report_id

    def pending(self, now=None):
        row = self.db.execute(
            """SELECT d.*,r.payload FROM deliveries d JOIN reports r ON r.id=d.report_id
            WHERE d.state='pending' AND d.next_attempt<=? ORDER BY d.report_id LIMIT 1""",
            (time.time() if now is None else now,),
        ).fetchone()
        return dict(row) if row else None

    def still_pending(self, report_id, session):
        row = self.db.execute(
            "SELECT state FROM deliveries WHERE report_id=? AND session=?",
            (report_id, session),
        ).fetchone()
        return row is not None and row["state"] == "pending"

    def metadata(self, identity):
        row = self.db.execute(
            "SELECT signature FROM thread_metadata WHERE identity=?", (identity,)
        ).fetchone()
        return row[0] if row else None

    def record_metadata(self, identity, signature):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO thread_metadata VALUES(?,?)",
                (identity, signature),
            )

    def settled(self, identity, detail):
        row = self.db.execute(
            "SELECT fingerprint FROM observations WHERE identity=?", (identity,)
        ).fetchone()
        view = progress_view(detail)
        return row is not None and row[0] == digest(
            dict(view, status=observation_status(view["status"]))
        )

    def delivered(self, report_id, session, success):
        with self.db:
            if success:
                self.db.execute(
                    "UPDATE deliveries SET state='handed_off' WHERE report_id=? AND session=?",
                    (report_id, session),
                )
            else:
                self.db.execute(
                    """UPDATE deliveries SET attempts=attempts+1,
                    next_attempt=?,state=CASE WHEN attempts>=7 THEN 'failed' ELSE 'pending' END
                    WHERE report_id=? AND session=?""",
                    (time.time() + 30, report_id, session),
                )

    def recent(self, after=0, limit=50):
        return [
            dict(id=row["id"], created=row["created"], **json.loads(row["payload"]))
            for row in self.db.execute(
                "SELECT * FROM reports WHERE id>? ORDER BY id LIMIT ?", (after, limit)
            )
        ]

    def delivery_counts(self):
        return {
            r["state"]: r["n"]
            for r in self.db.execute(
                "SELECT state,count(*) n FROM deliveries GROUP BY state"
            )
        }

    def clear_session_deliveries(self, origin):
        with self.db:
            self.db.execute(
                "DELETE FROM reports WHERE id IN (SELECT report_id FROM deliveries WHERE session=?) "
                "AND id NOT IN (SELECT report_id FROM deliveries WHERE session!=?)",
                (origin, origin),
            )
            self.db.execute("DELETE FROM deliveries WHERE session=?", (origin,))

    def prune(self):
        # Pending and failed deliveries stay available for troubleshooting.
        with self.db:
            self.db.execute(
                """DELETE FROM deliveries WHERE state IN ('sent','handed_off','superseded') AND report_id IN
                (SELECT id FROM reports WHERE created<?)""",
                (time.time() - 30 * 86400,),
            )
            self.db.execute(
                """DELETE FROM reports WHERE created<? AND id NOT IN
                (SELECT report_id FROM deliveries)""",
                (time.time() - 30 * 86400,),
            )


class GlobalMonitor:
    def __init__(self, desktop, store, jobs, settings, logger):
        self.desktop, self.store, self.jobs = desktop, store, jobs
        self.settings, self.logger = settings, logger
        self.last_scan = None
        self.last_error = None
        self.thread_count = 0
        self.unavailable_hosts = []

    async def scan(self):
        listing = await self.desktop.all_threads()
        self.unavailable_hosts = listing.get("unavailableHosts", [])
        self.thread_count = len(listing["threads"])
        sessions = (
            [self.settings["notification_session"]]
            if self.settings.get("notification_session")
            else []
        )
        owned = self.jobs.owned_threads()
        failures = []
        initialized = self.store.metadata("$initialized") is not None
        for row in listing["threads"]:
            if row.get("kind", "codex") != "codex":
                continue
            try:
                identity = f"{row.get('hostId', 'local')}:{row['id']}"
                signature = digest(
                    {
                        key: row.get(key)
                        for key in ("status", "updatedAt", "title", "archived")
                    }
                )
                previous = self.store.metadata(identity)
                if not is_active(row) and previous == signature:
                    continue
                if previous is None and not is_active(row) and not initialized:
                    detail = {"thread": row, "turns": []}
                else:
                    detail = await self.desktop.call(
                        "read_thread",
                        {
                            "threadId": row["id"],
                            "hostId": row.get("hostId", "local"),
                            "turnLimit": 1,
                            "includeOutputs": False,
                            "maxOutputCharsPerItem": 20000,
                        },
                    )
                self.store.observe(
                    row,
                    detail,
                    sessions,
                    self.settings["progress_interval_seconds"],
                    owned=row["id"] in owned,
                    report_new=initialized,
                )
                if self.store.settled(identity, detail):
                    self.store.record_metadata(identity, signature)
            except Exception as exc:
                failures.append(f"{row['id']}: {str(exc)[:160]}")
        self.last_scan = time.time()
        self.last_error = "; ".join(failures[:3]) or None
        self.store.record_metadata("$initialized", "1")
        self.store.prune()
