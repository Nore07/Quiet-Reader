"""Read the latest public, stable QuietReader release from GitHub."""

from dataclasses import dataclass
import json
import re

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from quietreader import __version__


PROJECT_URL = "https://github.com/Nore07/Quiet-Reader"
RELEASES_URL = f"{PROJECT_URL}/releases"
LATEST_RELEASE_API = "https://api.github.com/repos/Nore07/Quiet-Reader/releases/latest"
_CHECK_INTERVAL = 24 * 60 * 60
_MAX_REPLY_BYTES = 512 * 1024
_VERSION_TAG = re.compile(r"v?([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})")


@dataclass(frozen=True)
class ReleaseResult:
    status: str
    version: str
    url: str


def automatic_check_due(last_check: int, now: int) -> bool:
    return last_check <= 0 or not 0 <= now - last_check < _CHECK_INTERVAL


def read_release(payload: bytes, installed_version: str) -> ReleaseResult:
    if len(payload) > _MAX_REPLY_BYTES:
        raise ValueError("GitHub release response exceeds the size limit")
    release = json.loads(payload)
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Expected a published stable release")
    tag = release.get("tag_name")
    return _compare_release(tag, installed_version)


def read_release_redirect(url: str, installed_version: str) -> ReleaseResult:
    prefix = f"{RELEASES_URL}/tag/"
    if not url.startswith(prefix):
        raise ValueError("Expected a QuietReader release on GitHub")
    return _compare_release(url[len(prefix):], installed_version)


def _compare_release(tag, installed_version: str) -> ReleaseResult:
    remote = _VERSION_TAG.fullmatch(tag) if isinstance(tag, str) else None
    local = _VERSION_TAG.fullmatch(installed_version)
    if remote is None or local is None:
        raise ValueError("Expected numeric release versions")
    latest = tuple(map(int, remote.groups()))
    installed = tuple(map(int, local.groups()))
    status = "available" if latest > installed else "ahead" if latest < installed else "current"
    return ReleaseResult(status, ".".join(remote.groups()), f"{RELEASES_URL}/tag/{tag}")


class ReleaseChecker(QObject):
    finished = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._timeout = QTimer(self, singleShot=True, interval=8000)
        self._timeout.timeout.connect(self._abort)

    def check(self) -> bool:
        if self._reply is not None:
            return False
        self._request(latest_page=False)
        self._timeout.start()
        return True

    def _request(self, *, latest_page: bool):
        url = f"{RELEASES_URL}/latest" if latest_page else LATEST_RELEASE_API
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"QuietReader")
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        if latest_page:
            reply = self._network.head(request)
        else:
            request.setRawHeader(b"Accept", b"application/vnd.github+json")
            reply = self._network.get(request)
            reply.setReadBufferSize(_MAX_REPLY_BYTES + 1)
            reply.downloadProgress.connect(self._check_size)
        self._reply = reply
        reply.finished.connect(lambda: self._finish(reply, latest_page))

    def _check_size(self, received: int, total: int):
        if max(received, total) > _MAX_REPLY_BYTES:
            self._abort()

    def _abort(self):
        if self._reply is not None:
            self._reply.abort()

    def _finish(self, reply: QNetworkReply, latest_page: bool):
        if reply is not self._reply:
            return
        self._reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if not latest_page and status in (403, 429):
            # Shared networks can exhaust GitHub's anonymous API quota.
            reply.deleteLater()
            self._request(latest_page=True)
            return
        self._timeout.stop()
        result = None
        if reply.error() == QNetworkReply.NetworkError.NoError:
            try:
                if not latest_page and status == 200:
                    result = read_release(bytes(reply.readAll()), __version__)
                elif latest_page and status in (301, 302, 303, 307, 308):
                    target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
                    if isinstance(target, QUrl):
                        result = read_release_redirect(reply.url().resolved(target).toString(), __version__)
            except (ValueError, RecursionError):
                pass
        reply.deleteLater()
        self.finished.emit(result)

    def stop(self):
        self._timeout.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
