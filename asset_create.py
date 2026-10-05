"""Upload a binary Roblox model as a new Package through Open Cloud.

The API key is read only from ROBLOX_API_KEY. This creates a new model asset;
Roblox currently does not support updating .rbxm model content through this API.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ASSETS_URL = "https://apis.roblox.com/assets/v1/assets"
OPERATIONS_URL = "https://apis.roblox.com/assets/v1/operations"
MAX_BYTES = 20 * 1024 * 1024


def read_package(path: Path) -> bytes:
    if path.suffix.lower() != ".rbxm":
        raise ValueError("Package must be a binary .rbxm file")
    data = path.read_bytes()
    if not data.startswith(b"<roblox!"):
        raise ValueError("Package is not a binary Roblox model")
    if len(data) > MAX_BYTES:
        raise ValueError(f"Package is {len(data)} bytes; Open Cloud permits at most {MAX_BYTES}")
    return data


def multipart_body(metadata: dict, filename: str, file_bytes: bytes) -> tuple[str, bytes]:
    if any(char in filename for char in '\r\n"'):
        raise ValueError("Package filename contains an unsupported character")
    boundary = f"rbxpackage-{uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="request"\r\n'
        "Content-Type: application/json\r\n\r\n"
        f"{json.dumps(metadata, separators=(',', ':'))}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="fileContent"; filename="{filename}"\r\n'
        "Content-Type: model/x-rbxm\r\n\r\n"
    ).encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", head + file_bytes + f"\r\n--{boundary}--\r\n".encode()


def request_json(url: str, key: str, *, body: bytes | None = None, content_type: str | None = None) -> dict:
    headers = {"x-api-key": key, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Roblox API HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"Roblox API request failed: {exc.reason}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Roblox API returned a non-object response")
    return result


def create_asset(path: Path, data: bytes, key: str, creator_type: str, creator_id: int, name: str, description: str) -> str:
    metadata = {
        "assetType": "Model",
        "displayName": name,
        "description": description,
        "creationContext": {"creator": {creator_type: str(creator_id)}},
    }
    content_type, body = multipart_body(metadata, path.name, data)
    response = request_json(ASSETS_URL, key, body=body, content_type=content_type)
    operation_path = response.get("path") or response.get("name")
    if not isinstance(operation_path, str) or not re.fullmatch(r"operations/[A-Za-z0-9_-]+", operation_path):
        raise RuntimeError("Create Asset returned no valid operation path")
    return operation_path.split("/", 1)[1]


def await_asset_id(operation_id: str, key: str, timeout_seconds: int = 180) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        operation = request_json(f"{OPERATIONS_URL}/{operation_id}", key)
        if operation.get("done"):
            if operation.get("error"):
                raise RuntimeError("Roblox rejected the package; check the asset dashboard")
            response = operation.get("response") or {}
            asset_id = response.get("assetId") or str(response.get("path", "")).removeprefix("assets/")
            if not str(asset_id).isdigit():
                raise RuntimeError("Completed upload returned no asset ID")
            return str(asset_id)
        time.sleep(2)
    raise TimeoutError(f"Upload is still processing. Check operation {operation_id} later; do not submit it again.")
