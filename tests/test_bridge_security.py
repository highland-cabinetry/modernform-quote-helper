import pytest
from app.main import BUILD2QUOTE_EXTENSION_ID, BUILD2QUOTE_EXTENSION_ORIGIN, _require_build2quote_origin
from fastapi import HTTPException, Request


def request_with_origin(origin: str | None, extension_id: str | None = None) -> Request:
    headers = [] if origin is None else [(b"origin", origin.encode("ascii"))]
    if extension_id:
        headers.append((b"x-build2quote-extension-id", extension_id.encode("ascii")))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_only_the_stable_build2quote_extension_origin_is_accepted():
    _require_build2quote_origin(request_with_origin(BUILD2QUOTE_EXTENSION_ORIGIN))
    _require_build2quote_origin(request_with_origin(None, BUILD2QUOTE_EXTENSION_ID))

    with pytest.raises(HTTPException) as error:
        _require_build2quote_origin(request_with_origin("chrome-extension://unrecognized"))
    assert error.value.status_code == 403

    with pytest.raises(HTTPException):
        _require_build2quote_origin(request_with_origin(None))

    with pytest.raises(HTTPException):
        _require_build2quote_origin(request_with_origin(None, "unrecognized"))
