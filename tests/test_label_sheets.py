"""Tests for the label-sheet fetch/compose service (app/services/label_sheets.py).

Network is mocked — no real EasyPost label is fetched. Covers splitting good
images from failures, composing a PDF, and the empty-input guard.
"""
import io

import pytest

from app.services import label_sheets


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 600), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, content: bytes, ok: bool = True) -> None:
        self.content = content
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise RuntimeError("http 500")


def test_fetch_splits_images_from_failures(monkeypatch):
    png = _png()

    def fake_get(url, timeout=None):
        if url == "notimg":
            return _FakeResp(b"this is not an image")
        if url == "http500":
            return _FakeResp(b"", ok=False)
        return _FakeResp(png)

    monkeypatch.setattr(label_sheets.requests, "get", fake_get)
    images, failed = label_sheets.fetch_label_images(["ok1", "ok2", "notimg", "http500", ""])
    assert len(images) == 2
    assert set(failed) == {"notimg", "http500"}


def test_build_print_sheet_composes_pdf(monkeypatch):
    monkeypatch.setattr(
        label_sheets, "fetch_label_images", lambda urls: ([_png() for _ in urls], [])
    )
    result = label_sheets.build_print_sheet(
        ["a", "b"], template_key="avery_j8169", printer_type="laser"
    )
    assert result.pdf.startswith(b"%PDF")
    assert result.included == 2
    assert result.failed == []


def test_build_reports_partial_failures(monkeypatch):
    monkeypatch.setattr(
        label_sheets, "fetch_label_images", lambda urls: ([_png()], ["bad-url"])
    )
    result = label_sheets.build_print_sheet(
        ["good", "bad-url"], template_key="avery_j8169", printer_type="inkjet"
    )
    assert result.included == 1
    assert result.failed == ["bad-url"]


def test_build_raises_when_nothing_fetched(monkeypatch):
    monkeypatch.setattr(
        label_sheets, "fetch_label_images", lambda urls: ([], list(urls))
    )
    with pytest.raises(ValueError):
        label_sheets.build_print_sheet(["a", "b"], template_key="avery_j8169")
