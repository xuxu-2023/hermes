"""Tests for POST /api/files/attachment-upload and GET /files/{name}.

Follows the same conventions as test_web_server_files.py:
  - starlette.testclient.TestClient against ``web_server.app``
  - monkeypatch ``web_server._UPLOADS_DIR`` to ``tmp_path`` so no real
    ~/.hermes/uploads is ever touched
  - auth via ``web_server._SESSION_HEADER_NAME`` / ``web_server._SESSION_TOKEN``
    (same seam used by all other hermes_cli web_server tests)
  - ``_isolate_hermes_home`` autouse fixture from conftest keeps HERMES_HOME
    hermetic; we additionally redirect _UPLOADS_DIR explicitly
"""

import io

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_with_app_state():
    """Build a TestClient in loopback / no-gate mode.

    Mirrors _client_with_app_state() from test_web_server_files.py exactly
    so auth middleware defers to the ephemeral _SESSION_TOKEN path.
    """
    prev_auth_required = getattr(web_server.app.state, "auth_required", None)
    prev_bound_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client, prev_auth_required, prev_bound_host


def _restore_app_state(prev_auth_required, prev_bound_host):
    if prev_auth_required is None:
        if hasattr(web_server.app.state, "auth_required"):
            delattr(web_server.app.state, "auth_required")
    else:
        web_server.app.state.auth_required = prev_auth_required
    if prev_bound_host is None:
        if hasattr(web_server.app.state, "bound_host"):
            delattr(web_server.app.state, "bound_host")
    else:
        web_server.app.state.bound_host = prev_bound_host


def _close_client(client):
    close = getattr(client, "close", None)
    if close is not None:
        close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def upload_client(monkeypatch, tmp_path):
    """TestClient with _UPLOADS_DIR redirected to tmp_path/uploads."""
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(mode=0o700)
    monkeypatch.setattr(web_server, "_UPLOADS_DIR", uploads_dir)

    client, prev_auth, prev_host = _client_with_app_state()
    try:
        yield client, uploads_dir
    finally:
        _close_client(client)
        _restore_app_state(prev_auth, prev_host)


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """Bare authed client — for GET /files/{name} tests that set up files manually."""
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(mode=0o700)
    monkeypatch.setattr(web_server, "_UPLOADS_DIR", uploads_dir)

    client, prev_auth, prev_host = _client_with_app_state()
    try:
        yield client, uploads_dir
    finally:
        _close_client(client)
        _restore_app_state(prev_auth, prev_host)


# ---------------------------------------------------------------------------
# POST /api/files/attachment-upload — happy path
# ---------------------------------------------------------------------------

def test_upload_happy_path_response_contract(upload_client):
    """Upload a small file; response must contain name/path/url/size."""
    client, uploads_dir = upload_client
    body = b"hello attachment"
    r = client.post(
        "/api/files/attachment-upload",
        files={"file": ("hello.txt", io.BytesIO(body), "text/plain")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "hello.txt"
    assert data["url"] == "/files/hello.txt"
    assert data["size"] == len(body)
    dest = uploads_dir / data["name"]
    assert dest.exists()
    assert dest.read_bytes() == body


def test_upload_collision_suffix_on_second_upload(upload_client):
    """Second upload of the same filename gets a -1 suffix; third gets -2."""
    client, uploads_dir = upload_client

    r1 = client.post(
        "/api/files/attachment-upload",
        files={"file": ("data.bin", io.BytesIO(b"first"), "application/octet-stream")},
    )
    assert r1.status_code == 200
    assert r1.json()["name"] == "data.bin"

    r2 = client.post(
        "/api/files/attachment-upload",
        files={"file": ("data.bin", io.BytesIO(b"second"), "application/octet-stream")},
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "data-1.bin"

    r3 = client.post(
        "/api/files/attachment-upload",
        files={"file": ("data.bin", io.BytesIO(b"third"), "application/octet-stream")},
    )
    assert r3.status_code == 200
    assert r3.json()["name"] == "data-2.bin"

    # All three files exist on disk with distinct content.
    assert (uploads_dir / "data.bin").read_bytes() == b"first"
    assert (uploads_dir / "data-1.bin").read_bytes() == b"second"
    assert (uploads_dir / "data-2.bin").read_bytes() == b"third"


# ---------------------------------------------------------------------------
# POST /api/files/attachment-upload — authentication
# ---------------------------------------------------------------------------

def test_upload_without_token_returns_401(upload_client):
    """Request with no auth header must be rejected."""
    client, _ = upload_client
    # Build a new client that has no session header.
    bare = TestClient(web_server.app)
    try:
        r = bare.post(
            "/api/files/attachment-upload",
            files={"file": ("x.txt", io.BytesIO(b"data"), "text/plain")},
        )
        assert r.status_code in (401, 403)
    finally:
        _close_client(bare)


def test_upload_with_wrong_token_returns_401(upload_client):
    """Request with incorrect bearer token must be rejected."""
    client, _ = upload_client
    bad = TestClient(web_server.app)
    bad.headers["Authorization"] = "Bearer totally-wrong-token"
    try:
        r = bad.post(
            "/api/files/attachment-upload",
            files={"file": ("x.txt", io.BytesIO(b"data"), "text/plain")},
        )
        assert r.status_code in (401, 403)
    finally:
        _close_client(bad)


# ---------------------------------------------------------------------------
# POST /api/files/attachment-upload — oversize rejection
# ---------------------------------------------------------------------------

def test_upload_oversize_returns_413(monkeypatch, tmp_path):
    """A body exceeding _UPLOADS_MAX_BYTES must return 413.

    We monkeypatch _UPLOADS_MAX_BYTES to 16 bytes so we never need a real
    100 MB payload — the logic is the same regardless of the threshold.
    """
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(mode=0o700)
    monkeypatch.setattr(web_server, "_UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(web_server, "_UPLOADS_MAX_BYTES", 16)

    client, prev_auth, prev_host = _client_with_app_state()
    try:
        r = client.post(
            "/api/files/attachment-upload",
            files={"file": ("big.bin", io.BytesIO(b"x" * 17), "application/octet-stream")},
        )
        assert r.status_code == 413
        # The partially-written file must have been cleaned up.
        assert not (uploads_dir / "big.bin").exists()
    finally:
        _close_client(client)
        _restore_app_state(prev_auth, prev_host)


# ---------------------------------------------------------------------------
# POST /api/files/attachment-upload — filename sanitization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_name,expected_name", [
    # Path separators stripped — only basename survives.
    ("../../etc/passwd", "passwd"),
    # Backslash path separator.
    (r"foo\bar\secret.txt", "secret.txt"),
    # Forward slash inside name.
    ("subdir/evil.txt", "evil.txt"),
    # Empty-after-strip collapses to upload.bin.
    ("/", "upload.bin"),
])
def test_upload_filename_sanitization(upload_client, raw_name, expected_name):
    """Dangerous characters in filenames are stripped before storage.

    Note: raw control characters (NUL, 0x1f) are percent-encoded by the
    multipart transport layer before reaching the handler, so they cannot
    be injected literally via HTTP.  The sanitizer's control-char stripping
    is covered by the unit tests below.
    """
    client, uploads_dir = upload_client
    r = client.post(
        "/api/files/attachment-upload",
        files={"file": (raw_name, io.BytesIO(b"content"), "application/octet-stream")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == expected_name
    assert (uploads_dir / expected_name).exists()


# ---------------------------------------------------------------------------
# _sanitize_upload_filename unit tests — raw control characters
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # Null byte stripped.
    ("file\x00.txt", "file.txt"),
    # ASCII control char stripped.
    ("file\x1f.txt", "file.txt"),
    # Both together — control chars stripped, then leading dot stripped → "txt".
    ("\x00\x1f.txt", "txt"),
    # Forward slash stripped by basename extraction.
    ("dir/evil.txt", "evil.txt"),
    # Backslash treated as separator.
    (r"dir\evil.txt", "evil.txt"),
    # DEL character stripped.
    ("file\x7f.txt", "file.txt"),
    # Everything stripped → fallback.
    ("\x00\x1f\x7f", "upload.bin"),
])
def test_sanitize_upload_filename_unit(raw, expected):
    """_sanitize_upload_filename handles raw control chars and path components."""
    assert web_server._sanitize_upload_filename(raw) == expected


# ---------------------------------------------------------------------------
# GET /files/{name} — authentication
# ---------------------------------------------------------------------------

def test_get_uploaded_file_with_bearer_header(authed_client):
    """Authenticated GET with Bearer header returns file content."""
    client, uploads_dir = authed_client
    (uploads_dir / "sample.txt").write_bytes(b"sample content")

    r = client.get(
        "/files/sample.txt",
        headers={"Authorization": f"Bearer {web_server._SESSION_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.content == b"sample content"


def test_get_uploaded_file_with_query_token(authed_client):
    """Authenticated GET with ?token= query parameter returns file content."""
    client, uploads_dir = authed_client
    (uploads_dir / "sample.txt").write_bytes(b"sample content")

    # Remove session header so only ?token= provides auth.
    bare = TestClient(web_server.app)
    try:
        r = bare.get(
            "/files/sample.txt",
            params={"token": web_server._SESSION_TOKEN},
        )
        assert r.status_code == 200
        assert r.content == b"sample content"
    finally:
        _close_client(bare)


def test_get_unauthenticated_returns_401(authed_client):
    """GET with no auth at all must be rejected with 401."""
    client, uploads_dir = authed_client
    (uploads_dir / "sample.txt").write_bytes(b"sample content")

    bare = TestClient(web_server.app)
    try:
        r = bare.get("/files/sample.txt")
        assert r.status_code == 401
    finally:
        _close_client(bare)


def test_get_wrong_token_returns_401(authed_client):
    """GET with incorrect token (header or query) must be rejected."""
    client, uploads_dir = authed_client
    (uploads_dir / "sample.txt").write_bytes(b"sample content")

    bare = TestClient(web_server.app)
    bare.headers["Authorization"] = "Bearer wrong-token"
    try:
        r = bare.get("/files/sample.txt")
        assert r.status_code == 401
    finally:
        _close_client(bare)


# ---------------------------------------------------------------------------
# GET /files/{name} — 404 for missing file
# ---------------------------------------------------------------------------

def test_get_missing_file_returns_404(authed_client):
    """GET for a file that does not exist returns 404."""
    client, _ = authed_client
    r = client.get(
        "/files/does-not-exist.txt",
        headers={"Authorization": f"Bearer {web_server._SESSION_TOKEN}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /files/{name} — path traversal guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("traversal_name", [
    "../outside.txt",
    "..%2Foutside.txt",
    "subdir/../../outside.txt",
])
def test_get_traversal_attempt_does_not_serve_outside_uploads_dir(
    authed_client, tmp_path, traversal_name
):
    """Path traversal attempts must not escape the uploads directory.

    Either a 404 (target resolves outside dir) or 400 (invalid name) is
    acceptable — what matters is that file content is never returned.
    """
    client, uploads_dir = authed_client
    # Place a file just outside the uploads dir — it must never be served.
    secret = tmp_path / "outside.txt"
    secret.write_bytes(b"secret outside content")

    r = client.get(
        f"/files/{traversal_name}",
        headers={"Authorization": f"Bearer {web_server._SESSION_TOKEN}"},
    )
    assert r.status_code in (400, 404)
    # Definitely must not return the secret content.
    assert b"secret outside content" not in r.content


# ---------------------------------------------------------------------------
# GET /files/{name} — .apk MIME type
# ---------------------------------------------------------------------------

def test_get_apk_file_has_correct_media_type(authed_client):
    """Files with .apk extension must be served with the Android APK MIME type."""
    client, uploads_dir = authed_client
    (uploads_dir / "app.apk").write_bytes(b"PK\x03\x04fake-apk")

    r = client.get(
        "/files/app.apk",
        headers={"Authorization": f"Bearer {web_server._SESSION_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.android.package-archive"
    )
