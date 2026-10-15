"""Executed inside a disposable synthetic container by docker_e2e.py."""
import json
import time
import urllib.error
import urllib.request
import jwt

def token(subject="alice", tenant="tenant-a", permissions=None):
    now = int(time.time())
    return jwt.encode({"sub": subject, "tenant_id": tenant, "iat": now,
                       "exp": now + 300, "iss": "https://synthetic.invalid",
                       "aud": "docker-synthetic", "permissions": permissions if permissions is not None else PERMISSIONS},
                      CONFIG["private"], algorithm="RS256")

def request(path, body, expected=200, subject="alice", tenant="tenant-a", permissions=None, bearer=True, method="POST", extra=None):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + token(subject, tenant, permissions)
    headers.update(extra or {})
    req = urllib.request.Request("http://127.0.0.1:8000" + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read()
    if status != expected:
        raise RuntimeError(f"{path}: expected {expected}, got {status}")
    return json.loads(payload) if payload else None

def verify(condition, message):
    if not condition:
        raise RuntimeError(message)

PERMISSIONS = ["documents:write", "knowledge:query"]
body = {"document_id": "synthetic-policy", "title": "Synthetic access policy", "text": "Managers approve synthetic access requests.", "readers": ["bob"]}
query = {"question": "Who approves synthetic access requests?", "top_k": 4}
request("/documents", body, 401, bearer=False)
request("/documents", body, 403, permissions=[])
if CONFIG["phase"] == "create":
    request("/documents", body, 201)
result = request("/query", query, subject="bob")
verify(bool(result["citations"]), "Authorized evidence missing after restart")
verify(result["generation"] == "extractive-demo", "Unexpected model invocation")
verify(not request("/query", query, subject="mallory")["citations"], "Reader ACL leak")
verify(not request("/query", query, tenant="tenant-b")["citations"], "Tenant leak")
request("/documents/synthetic-policy", None, 404, tenant="tenant-b", method="DELETE")
if CONFIG["phase"] == "verify":
    request("/documents/synthetic-policy", None, 204, method="DELETE")
    verify(not request("/query", query, subject="bob")["citations"], "Deleted evidence remains visible")

print("Synthetic phase passed:", CONFIG["phase"])
