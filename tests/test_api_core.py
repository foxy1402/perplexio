import sys


def test_health_and_pwa_routes(client):
    tc, _main = client

    r = tc.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    manifest = tc.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert "/icons/icon.png" in manifest.text
    assert manifest.headers.get("content-type", "").startswith("application/manifest+json")

    icon = tc.get("/icons/icon.png")
    assert icon.status_code == 200
    assert icon.headers.get("content-type", "").startswith("image/png")
    assert len(icon.content) > 0


def test_upload_list_download_and_purge(client):
    tc, _main = client

    up = tc.post(
        "/api/files/upload?async_index=true",
        files={"file": ("note.txt", b"hello upload", "text/plain")},
    )
    assert up.status_code == 200
    payload = up.json()
    fid = int(payload["file_id"])
    assert fid > 0

    files = tc.get("/api/files?limit=10")
    assert files.status_code == 200
    items = files.json()
    assert any(int(x["id"]) == fid for x in items)

    dl = tc.get(f"/api/files/{fid}/download")
    assert dl.status_code == 200
    assert dl.content == b"hello upload"

    purge = tc.post("/api/admin/purge", json={"confirm": True})
    assert purge.status_code == 200

    after = tc.get("/api/files?limit=10")
    assert after.status_code == 200
    assert after.json() == []


def test_ask_success_persists_chat(client, monkeypatch):
    tc, main = client
    Citation = sys.modules["app.models"].Citation

    async def fake_prepare_ask(**_kwargs):
        citations = [
            Citation(
                title="Example",
                url="https://example.com",
                snippet="example snippet",
            )
        ]
        return [{"role": "user", "content": "Q"}], citations

    async def fake_ask_model(_messages):
        return "Mock answer."

    async def fake_align(answer, _citations):
        return answer + " [1]"

    def fake_confidence(_answer, _citations):
        return 0.9, False

    monkeypatch.setattr(main, "prepare_ask", fake_prepare_ask)
    monkeypatch.setattr(main, "ask_model", fake_ask_model)
    monkeypatch.setattr(main, "align_answer_citations", fake_align)
    monkeypatch.setattr(main, "compute_answer_confidence", fake_confidence)

    r = tc.post(
        "/api/ask",
        json={"query": "What is this?", "include_files": False, "search_mode": "all"},
    )
    assert r.status_code == 200
    data = r.json()
    assert int(data["chat_id"]) > 0
    assert int(data["thread_id"]) > 0
    assert len(data["citations"]) == 1
    assert "Confidence:" in data["answer"]

    chats = tc.get("/api/chats?limit=5")
    assert chats.status_code == 200
    assert len(chats.json()) == 1


def test_ask_rejects_unknown_file_ids_without_saving_chat(client, monkeypatch):
    tc, main = client

    async def should_not_run(**_kwargs):
        raise AssertionError("prepare_ask should not run when file_ids are invalid")

    monkeypatch.setattr(main, "prepare_ask", should_not_run)

    r = tc.post(
        "/api/ask",
        json={
            "query": "test invalid file ids",
            "include_files": True,
            "file_ids": [999999],
            "search_mode": "all",
        },
    )
    assert r.status_code == 400
    assert "Unknown file ids" in r.text

    chats = tc.get("/api/chats?limit=20")
    assert chats.status_code == 200
    assert chats.json() == []
