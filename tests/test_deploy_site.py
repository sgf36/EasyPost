"""packaging/deploy_site.py must address a file in a subfolder as its folder
plus a bare filename.

cPanel's Fileman calls take a directory and a filename, not a path. Sent a path
as the filename they fail two different ways, and neither looks like the real
fault: save_file_content refuses it as "invalid characters", and upload_files
reports success while writing the file into the docroot instead of the folder.
On 2026-09-11 that second route put eight new screenshots at the site root and
left the real ones unchanged. The fix had been written on 2026-08-21 and never
reached main, which is why these tests exist rather than a comment.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Loaded by path: a top-level "packaging" import would find the pip package.
_spec = importlib.util.spec_from_file_location("deploy_site", ROOT / "packaging" / "deploy_site.py")
deploy_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploy_site)
DOCROOT = deploy_site.REMOTE_DIR


@pytest.mark.parametrize(
    "name, expected",
    [
        ("download.html", (DOCROOT, "download.html")),
        ("de/index.html", (f"{DOCROOT}/de", "index.html")),
        ("screenshots/settings.webp", (f"{DOCROOT}/screenshots", "settings.webp")),
    ],
)
def test_names_split_into_folder_and_bare_filename(name, expected):
    assert deploy_site._remote_parts(name) == expected


def _site(tmp_path, monkeypatch, rel, data):
    local = tmp_path / "site" / rel
    local.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        local.write_bytes(data)
    else:
        local.write_text(data, encoding="utf-8")
    monkeypatch.setattr(deploy_site, "SITE_DIR", tmp_path / "site")


@pytest.mark.parametrize("rel", ["index.html", "de/index.html"])
def test_a_page_is_saved_into_its_own_folder(tmp_path, monkeypatch, rel):
    _site(tmp_path, monkeypatch, rel, "<html>x</html>")
    calls = []
    monkeypatch.setattr(
        deploy_site, "uapi", lambda token, module, function, **params: calls.append((function, params)) or {}
    )
    monkeypatch.setattr(deploy_site, "_verify_over_https", lambda name, content: (True, "served"))

    assert deploy_site.upload("token", rel)
    ((function, params),) = calls
    assert function == "save_file_content"
    assert (params["dir"], params["file"]) == deploy_site._remote_parts(rel)
    assert "/" not in params["file"]


@pytest.mark.parametrize("rel", ["favicon.ico", "screenshots/settings.webp"])
def test_a_binary_is_uploaded_into_its_own_folder(tmp_path, monkeypatch, rel):
    raw = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    _site(tmp_path, monkeypatch, rel, raw)
    posted = {}

    def fake_post(url, **kwargs):
        posted["dir"] = kwargs["data"]["dir"]
        posted["filename"] = kwargs["files"]["file-1"][0]
        response = MagicMock()
        response.json.return_value = {"status": 1, "data": {"uploads": [{"status": 1}]}}
        return response

    served = MagicMock()
    served.content = raw
    monkeypatch.setattr(deploy_site.requests, "post", fake_post)
    monkeypatch.setattr(deploy_site.requests, "get", lambda *args, **kwargs: served)

    assert deploy_site.upload("token", rel)
    assert (posted["dir"], posted["filename"]) == deploy_site._remote_parts(rel)
    assert "/" not in posted["filename"]


def test_an_executed_file_is_read_back_from_its_own_folder(tmp_path, monkeypatch):
    rel = "tools/contact.php"
    _site(tmp_path, monkeypatch, rel, "<?php echo 1;")
    calls = []

    def fake_uapi(token, module, function, **params):
        calls.append((function, params))
        return {"content": "<?php echo 1;"} if function == "get_file_content" else {}

    monkeypatch.setattr(deploy_site, "uapi", fake_uapi)

    assert deploy_site.upload("token", rel)
    for function, params in calls:
        assert (params["dir"], params["file"]) == (f"{DOCROOT}/tools", "contact.php"), function
