"""Open a label URL or file path, without launching Safari on Mac App Store builds.

The Mac App Store sandbox and Apple's Guideline 4 (Design) require that the app
never open the default web browser.  Web URLs (EasyPost label downloads) are
fetched into a temporary file and opened with the system's native handler
(Preview for PDFs, an image viewer for PNGs), satisfying both the sandbox and
the review guideline.

Non-MAS builds keep the existing ``webbrowser.open`` behaviour.
"""

import logging
import tempfile
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from app.config import MAS_BUILD

log = logging.getLogger(__name__)

_CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/zpl": ".zpl",
    "text/plain": ".txt",
}


def open_label(url_or_path: str, file_type: Optional[str] = None) -> None:
    """Open a label URL or local file path.

    *url_or_path* is either a web URL (``https://…``), a ``file://`` URI or a
    plain filesystem path.  *file_type* is an optional MIME hint (e.g.
    ``"application/pdf"``) used when the URL or Content-Type header is ambiguous.

    On MAS builds, web URLs are downloaded to a temporary file and opened with
    the native file handler — Safari is never launched.  On every other build
    the call delegates to :func:`webbrowser.open`.
    """
    if not MAS_BUILD:
        # Direct-download / Microsoft Store: open in the default browser or
        # file handler, exactly as the app has always done.
        if not url_or_path.startswith(("http://", "https://")):
            webbrowser.open(Path(url_or_path).as_uri())
        else:
            webbrowser.open(url_or_path)
        return

    # --- Mac App Store: never open Safari ---
    if url_or_path.startswith(("http://", "https://")):
        _open_url_locally(url_or_path, file_type)
    else:
        # Local path or file:// URI — open with the native handler directly.
        path = url_or_path
        if path.startswith("file://"):
            path = QUrl(path).toLocalFile()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))


def _open_url_locally(url: str, file_type: Optional[str] = None) -> None:
    """Download *url* to a temporary file and open it with the native handler."""
    import urllib.request

    try:
        resp = urllib.request.urlopen(url, timeout=30)  # noqa: S310
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
    except Exception:
        log.warning("Could not download label from %s", url, exc_info=True)
        return

    ext = _CONTENT_TYPE_EXT.get(file_type or content_type, "")
    if not ext:
        ext = Path(urlparse(url).path).suffix or ".pdf"

    with tempfile.NamedTemporaryFile(
        prefix="easypost-label-", suffix=ext, delete=False
    ) as f:
        f.write(data)
        local_path = f.name

    QDesktopServices.openUrl(QUrl.fromLocalFile(local_path))
