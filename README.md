# Enterprise Knowledge Assistant

A citation-first retrieval API with authenticated tenant isolation, document
access controls, encrypted persistence, and optional bounded Gemini generation.
Local mode returns source excerpts without pretending a model ran.

## Architecture and boundaries

Documents are stored as encrypted records in SQLite. Every query loads only the
verified tenant's unexpired documents, filters reader access, and constructs a
bounded BM25 index from that authorized snapshot before retrieval or model use.
There is no shared process-global corpus or persistent derived index to leave
stale content behind after deletion.

- RS256 token verification and explicit endpoint permissions.
- Versioned document updates; duplicates return 409.
- Document owners control updates/deletion; `documents:admin` additionally permits
  management of other owners' documents within the same tenant.
- Empty `readers` means tenant-readable; a nonempty list restricts access to the
  owner and listed token subject IDs. Admin permission does not bypass query ACLs.
- Encrypted document text, metadata, and ACLs; bounded tenant corpus and request sizes.
- Optional model output uses a typed answer/citation contract. Unknown source IDs,
  missing citations, malformed output, and model errors produce extractive fallback.
- A valid citation ID is not proof that every generated claim is semantically true.
  Review evidence and run domain-specific grounding evaluation before relying on prose.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install --no-deps -e .
pytest -q
uvicorn knowledge_assistant.app:app --host 127.0.0.1
```

Set `AUTH_PUBLIC_KEY` (RSA PEM), `AUTH_ISSUER`, `AUTH_AUDIENCE`,
`KNOWLEDGE_DATA_DIR`, and `DATA_ENCRYPTION_KEY`. Use a stable Fernet key from a
secret manager; generate a new one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
Keep it across restarts and backup restores. Protected requests fail closed if
identity or encrypted storage is missing. `/health` is public; interactive docs
are disabled. See [SECURITY.md](SECURITY.md).

## API

Send an identity-provider token in `Authorization: Bearer <token>`. Required
claims are `sub`, `tenant_id`, `iat`, `exp`, `iss`, `aud`, and `permissions`.

`documents:write` permits `POST /documents`:

```json
{"document_id":"access-policy","title":"Access policy","text":"Employees request manager approval.","readers":["reader-subject-id"],"retention_days":30}
```

Document IDs contain letters, numbers, dots, underscores, and hyphens (up to 120
characters). Omit `readers` for tenant-wide reading. Each document is bounded to
100,000 characters; tenant storage is capped at 100 documents and 1,000,000 UTF-8
text bytes. Request bodies are independently capped at 300,000 bytes.

`knowledge:query` permits `POST /query`:

```json
{"question":"Who approves access?","top_k":4}
```

The answer contains authorized citations and `generation` mode.
`PUT /documents/access-policy?expected_version=1` replaces a document using the
same body shape and increments its version. `DELETE /documents/access-policy`
removes it from subsequent queries. Cross-tenant and unauthorized management
requests return 404; stale versions return 409.

## Optional model

The lockfile includes the optional SDK. Configure approved Google Application
Default Credentials, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, an available
`GEMINI_MODEL`, and `GEMINI_ENABLED=true`. Only authorized retrieved passages are
sent, but they may contain sensitive data. Obtain approval for that data flow.
Calls have a 10-second SDK timeout, one attempt, and bounded output; provider
failures retain authorized extractive evidence without exposing error text.

## Retention and operations

`retention_days` is 1–365, default 30, reset on a successful update. Expired
records disappear from queries immediately. Run a daily operator job:

```bash
python -m knowledge_assistant.maintenance purge-expired
```

Use one persistent local volume; SQLite is not a multi-host database. Backups
must remain encrypted and expire under an explicit policy. Text JSON is the only
ingestion format; adding binary uploads requires quarantine, type/size validation,
and malware scanning before indexing.

Tests cover tenant/reader separation, model-boundary filtering, version conflicts,
update/deletion, persistence, encryption, expiry, concurrency, citation rejection,
authentication, quotas, and request bounds. CI audits the dependency lock and scans
source. Before external deployment, configure TLS, distributed quotas, required
checks, recovery objectives and monitoring. Embeddings, reranking, connectors,
and a UI are optional enhancements after access and grounding evaluation.
