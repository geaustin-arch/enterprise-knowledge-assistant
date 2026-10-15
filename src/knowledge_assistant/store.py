"""Tenant-scoped encrypted SQLite document repository; no shared in-memory corpus."""

from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import json
import os
import sqlite3
import time
from cryptography.fernet import Fernet
from .core import chunk_document


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class QuotaExceeded(Exception):
    pass


class DocumentStore:
    def __init__(self, directory, key):
        self.key = key.encode()
        self.cipher = Fernet(self.key)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.stat().st_mode & 0o077 or directory.stat().st_uid != os.getuid():
            raise PermissionError(
                "Storage requires a private directory owned by the service identity"
            )
        self.path = directory / "documents.sqlite3"
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        os.chmod(self.path, 0o600)
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS documents (tenant TEXT NOT NULL, document TEXT NOT NULL, expires REAL NOT NULL, bytes INTEGER NOT NULL, payload BLOB NOT NULL, PRIMARY KEY (tenant, document))"
            )

    def digest(self, *parts):
        return hmac.new(
            self.key, json.dumps(parts).encode(), hashlib.sha256
        ).hexdigest()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA secure_delete=ON")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def decode(self, row):
        return json.loads(self.cipher.decrypt(row[0]))

    def put(self, tenant, actor, body, expected_version=None, admin=False):
        tenant_key, doc_key = (
            self.digest(tenant),
            self.digest(tenant, body["document_id"]),
        )
        size = len(body["text"].encode())
        if len(chunk_document(body["document_id"], body["title"], body["text"])) == 0:
            raise ValueError("Empty document")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM documents WHERE tenant=? AND expires<=?",
                (tenant_key, time.time()),
            )
            row = conn.execute(
                "SELECT payload, bytes FROM documents WHERE tenant=? AND document=?",
                (tenant_key, doc_key),
            ).fetchone()
            if expected_version is None and row:
                raise Conflict("document_id already exists")
            if expected_version is not None:
                if not row:
                    raise NotFound()
                old = self.decode(row)
                if old["owner"] != actor and not admin:
                    raise NotFound()
                if old["version"] != expected_version:
                    raise Conflict("Document version changed")
            count, total = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM documents WHERE tenant=?",
                (tenant_key,),
            ).fetchone()
            if (
                count + (0 if row else 1) > 100
                or total - (row[1] if row else 0) + size > 1_000_000
            ):
                raise QuotaExceeded()
            data = {
                **body,
                "owner": self.decode(row)["owner"] if row else actor,
                "version": (self.decode(row)["version"] + 1) if row else 1,
            }
            payload = self.cipher.encrypt(json.dumps(data).encode())
            conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
                (
                    tenant_key,
                    doc_key,
                    time.time() + body["retention_days"] * 86400,
                    size,
                    payload,
                ),
            )
            return data["version"]

    def chunks(self, tenant, actor):
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM documents WHERE tenant=? AND expires>? ORDER BY document",
                (self.digest(tenant), time.time()),
            ).fetchall()
        chunks = []
        for row in rows:
            data = self.decode(row)
            if (
                data["readers"]
                and actor != data["owner"]
                and actor not in data["readers"]
            ):
                continue
            chunks.extend(
                chunk_document(data["document_id"], data["title"], data["text"])
            )
        return chunks

    def delete(self, tenant, actor, document_id, admin=False):
        keys = (self.digest(tenant), self.digest(tenant, document_id))
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM documents WHERE tenant=? AND document=? AND expires>?",
                (*keys, time.time()),
            ).fetchone()
            if not row or (self.decode(row)["owner"] != actor and not admin):
                raise NotFound()
            conn.execute("DELETE FROM documents WHERE tenant=? AND document=?", keys)

    def purge_expired(self, now=None):
        with self.connection() as conn:
            return conn.execute(
                "DELETE FROM documents WHERE expires<=?",
                (time.time() if now is None else now,),
            ).rowcount
