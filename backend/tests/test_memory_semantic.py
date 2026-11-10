"""Optional semantic embeddings for memory retrieval: configuration (permissions, supported providers, audit),
background indexing, ranking with the model's vectors, invalidation on correction, and fallback to the hashed
embedding when the provider fails. The provider is simulated at the HTTP layer (httpx.MockTransport) by a toy
"model" that maps words with the same meaning to the same direction — it stands in for a real embedding model."""
from __future__ import annotations

import json
import uuid

import httpx2 as httpx
import pytest

from app.memory import semantic, store
from app.models import Memory, Organization, Project, Provider

CONCEPTS = {"car": 0, "automobile": 0, "vehicle": 0, "insurance": 1, "policy": 1, "coverage": 1,
            "database": 2, "postgres": 2, "storage": 2, "deploy": 3, "release": 3, "rollout": 3}


class FakeEmbeddings:
    def __init__(self):
        self.calls: list[dict] = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append({"url": str(request.url), "body": body, "auth": request.headers.get("authorization")})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "down"})
        data = []
        for i, text in enumerate(body["input"]):
            v = [0.0] * 5
            for w in text.lower().replace("\n", " ").split():
                v[CONCEPTS.get(w.strip(".,?"), 4)] += 1.0
            data.append({"index": i, "embedding": v, "object": "embedding"})
        return httpx.Response(200, json={"data": data, "model": body["model"]})


@pytest.fixture
def fake(monkeypatch, db):
    f = FakeEmbeddings()
    monkeypatch.setattr(semantic, "HTTP_TRANSPORT", httpx.MockTransport(f))
    semantic.reset()
    yield f
    semantic.reset()
    org = db.get(Organization, 1)
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "memory"}
    db.commit()


def _setup(db):
    p = Project(organization_id=1, name=f"Sem {uuid.uuid4().hex[:6]}")
    prov = Provider(organization_id=1, name=f"local embed {uuid.uuid4().hex[:6]}", kind="openai_compatible",
                    base_url="http://embed.test/v1")
    db.add_all([p, prov])
    db.flush()
    mems = {}
    for key, title, content in [("car", "Vehicle cover", "We buy car insurance from the broker every spring."),
                                ("db", "Persistence", "The service keeps state in postgres with nightly backups."),
                                ("ship", "Shipping", "Each release goes through a staged rollout.")]:
        mems[key] = store.create(db, organization_id=1, memory_type="technical_decision", title=title, content=content,
                                 source={"kind": "user", "ref": "user:1"}, verification="verified", project_id=p.id)
    db.commit()
    return p, prov, mems


def _search(db, p, q):
    return store.search(db, organization_id=1, query=q, project_id=p.id, limit=3)


def test_paraphrase_is_found_with_model_vectors_and_not_without(admin_client, db, fake):
    p, prov, m = _setup(db)
    q = "automobile coverage"  # shares no word with the memory it means
    before = _search(db, p, q)
    assert all(h.method == "hashed" for h in before) and fake.calls == []  # off by default: nothing sent anywhere
    r = admin_client.put("/api/memory/embedding", json={"provider_id": prov.id, "model": "toy-embed"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True and r.json()["pending"] >= 3
    r = admin_client.post("/api/memory/embedding/reindex")
    assert r.status_code == 200 and r.json()["indexed"] >= 3 and r.json()["pending"] == 0
    assert fake.calls[0]["url"] == "http://embed.test/v1/embeddings" and fake.calls[0]["body"]["model"] == "toy-embed"
    db.expire_all()  # the reindex ran in its own session
    after = _search(db, p, q)
    assert after[0].memory.id == m["car"].id and after[0].method == "model"
    assert _search(db, p, "where is storage kept")[0].memory.id == m["db"].id
    hits = admin_client.get("/api/memory/search", params={"q": q, "project_id": p.id}).json()
    assert hits[0]["id"] == m["car"].id and hits[0]["match"] == "model"
    # queries are cached: the same search does not call the provider again
    n = len(fake.calls)
    _search(db, p, q)
    assert len(fake.calls) == n


def test_correction_clears_the_vector_until_reindexed(admin_client, db, fake):
    _p, prov, m = _setup(db)
    admin_client.put("/api/memory/embedding", json={"provider_id": prov.id, "model": "toy-embed"})
    admin_client.post("/api/memory/embedding/reindex")
    r = admin_client.post(f"/api/memory/{m['ship'].id}/correct", json={"content": "Releases use a canary rollout."})
    assert r.status_code == 200, r.text
    new = db.get(Memory, r.json()["id"])
    assert new.semantic_embedding is None and new.semantic_model is None
    assert admin_client.get("/api/memory/embedding").json()["pending"] == 1
    assert semantic.backfill(1)["indexed"] == 1
    db.expire_all()
    assert db.get(Memory, new.id).semantic_model == f"{prov.id}:toy-embed"
    # another model makes every vector stale
    admin_client.put("/api/memory/embedding", json={"provider_id": prov.id, "model": "toy-embed-v2"})
    assert admin_client.get("/api/memory/embedding").json()["pending"] >= 3


def test_provider_failure_falls_back_to_hashed_and_breaks_the_circuit(admin_client, db, fake):
    p, prov, m = _setup(db)
    admin_client.put("/api/memory/embedding", json={"provider_id": prov.id, "model": "toy-embed"})
    admin_client.post("/api/memory/embedding/reindex")
    fake.status = 503
    hits = _search(db, p, "a brand new query")
    assert hits and all(h.method == "hashed" for h in hits)  # retrieval keeps working
    n = len(fake.calls)
    _search(db, p, "another new query")
    assert len(fake.calls) == n  # skipped while the breaker is open: no repeated slow calls
    assert admin_client.post(f"/api/memory/{m['db'].id}/correct", json={"content": "State lives in postgres."}).status_code == 200
    res = semantic.backfill(1)
    assert res["failed"] >= 1 and res["indexed"] == 0
    assert admin_client.get("/api/memory/embedding").json()["pending"] >= 1  # retried later by the worker


def test_configuration_rules(admin_client, client, db, fake, make_user):
    from app.models import AuditLog as ActivityLog
    _, prov, _ = _setup(db)
    anth = Provider(organization_id=1, name=f"anth {uuid.uuid4().hex[:6]}", kind="anthropic")
    db.add(anth)
    db.commit()
    r = admin_client.put("/api/memory/embedding", json={"provider_id": anth.id, "model": "x"})
    assert r.status_code == 422 and "embeddings" in r.json()["detail"]
    assert admin_client.put("/api/memory/embedding", json={"provider_id": 999999, "model": "x"}).status_code == 422
    assert admin_client.put("/api/memory/embedding", json={"provider_id": prov.id}).status_code == 422
    assert admin_client.post("/api/memory/embedding/reindex").status_code == 409
    admin_client.put("/api/memory/embedding", json={"provider_id": prov.id, "model": "toy-embed"})
    audit = db.query(ActivityLog).filter(ActivityLog.action == "memory.embedding_configured").order_by(
        ActivityLog.id.desc()).first()
    assert audit and audit.details["model"] == "toy-embed"
    assert admin_client.put("/api/memory/embedding", json={"provider_id": None}).json()["enabled"] is False
    # a developer may read the state but not change where memory text is sent
    from tests.conftest import login_as
    make_user("dev-sem", "DEVELOPER", password="dev-sem-password-1")
    login_as(client, "dev-sem", "dev-sem-password-1")
    assert client.get("/api/memory/embedding").status_code == 200
    assert client.put("/api/memory/embedding", json={"provider_id": prov.id, "model": "m"}).status_code == 403
