import json

import pytest

from tracefix.runner_ui.server import _parse_uploaded_json_upload


def _multipart(filename: str, payload: bytes, content_type: str = "application/json") -> tuple[str, bytes]:
    boundary = "----tracefix-test-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        "\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", body


def test_parse_valid_json_upload() -> None:
    content_type, body = _multipart("room_state.json", json.dumps({"people_count": 2}).encode("utf-8"))

    filename, parsed, size = _parse_uploaded_json_upload(content_type, body)

    assert filename == "room_state.json"
    assert parsed == {"people_count": 2}
    assert size > 0


def test_parse_invalid_json_upload_rejected() -> None:
    content_type, body = _multipart("room_state.json", b"{not-json")

    with pytest.raises(ValueError, match="Malformed JSON upload"):
        _parse_uploaded_json_upload(content_type, body)


def test_parse_non_json_extension_rejected() -> None:
    content_type, body = _multipart("room_state.txt", b"{}")

    with pytest.raises(ValueError, match=".json extension"):
        _parse_uploaded_json_upload(content_type, body)
