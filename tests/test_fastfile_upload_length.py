"""Fastfile: streamed uploads get a Content-Length.

firebase_app_distribution (google-apis-core) streams the binary without a
length; Net::HTTP on Ruby 3.4+/4.0 raises "Content-Length not given and
Transfer-Encoding is not `chunked'" right after "Uploading the APK"."""
import re
from pathlib import Path

FASTFILE = Path(__file__).resolve().parent.parent / "fastlane" / "Fastfile"


def test_fastfile_sets_content_length_for_body_streams():
    text = FASTFILE.read_text(encoding="utf-8")
    assert "Net::HTTPGenericRequest.prepend(ToolkitBodyStreamLength)" in text
    assert re.search(r"def send_request_with_body_stream\(sock, ver, path, f", text)
    assert "self.content_length = size" in text
    # Applied before any lane runs: above the first lane definition.
    assert text.index("Net::HTTPGenericRequest.prepend") < text.index("lane :")


def test_fastfile_is_valid_ruby():
    import shutil
    import subprocess

    import pytest

    ruby = shutil.which("ruby")
    if not ruby:
        pytest.skip("no ruby on this machine")
    r = subprocess.run([ruby, "-c", str(FASTFILE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
