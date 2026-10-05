"""Inspect or publish a new version of an existing Roblox .rbxm Package outside Studio.

Uses Roblox's cookie-auth asset endpoint, not the API-key Open Cloud endpoint
(which currently refuses .rbxm content updates). No credential is written to disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from asset_create import multipart_body, read_package


BASE = "https://apis.roblox.com/assets/user-auth/v1"
VERSIONS = BASE + "/assets/{}/versions"
OPERATION_RE = re.compile(r"(?:operations/)?([A-Za-z0-9_-]+)")


class ApiError(RuntimeError):
    def __init__(self, status: int, csrf: str | None = None):
        super().__init__(f"Roblox API returned HTTP {status}")
        self.status = status
        self.csrf = csrf


def auth_cookie() -> str:
    raw = os.environ.get("RBX_COOKIE") or os.environ.get("ROBLOX_AUTH_COOKIE")
    if not raw:
        raise RuntimeError("Set RBX_COOKIE in your local environment; never pass it on the command line")
    raw = raw.strip()
    return raw if raw.startswith(".ROBLOSECURITY=") else ".ROBLOSECURITY=" + raw


def api_request(
    url: str, cookie: str, *, method: str = "GET", body: bytes | None = None,
    content_type: str | None = None, csrf: str | None = None,
) -> dict:
    headers = {"Cookie": cookie, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if csrf:
        headers["x-csrf-token"] = csrf
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=90) as response:
            payload = response.read()
    except HTTPError as exc:
        # Avoid printing response bodies or headers; they may contain account data.
        raise ApiError(exc.code, exc.headers.get("x-csrf-token")) from None
    except URLError as exc:
        raise RuntimeError("Network request outcome is unknown; inspect the asset before retrying") from exc
    if not payload:
        return {}
    try:
        result = json.loads(payload)
    except ValueError as exc:
        raise RuntimeError("Roblox returned a non-JSON response; inspect the asset before retrying") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Roblox returned a non-object response")
    return result


def asset_owner(metadata: dict) -> tuple[str | None, str | None]:
    creator = (metadata.get("creationContext") or {}).get("creator") or metadata.get("creator") or {}
    if not isinstance(creator, dict):
        return None, None
    if creator.get("groupId") is not None:
        return "group", str(creator["groupId"])
    if creator.get("creatorType") in ("Group", "GROUP") and creator.get("creatorId") is not None:
        return "group", str(creator["creatorId"])
    if creator.get("userId") is not None:
        return "user", str(creator["userId"])
    return None, None


def inspect(asset_id: int, owner_type: str, owner_id: int, cookie: str) -> tuple[dict, dict]:
    metadata = api_request(f"{BASE}/assets/{asset_id}", cookie)
    path = metadata.get("path")
    returned_id = metadata.get("assetId") or metadata.get("id")
    if returned_id is None and isinstance(path, str):
        returned_id = path.removeprefix("assets/")
    if str(returned_id) != str(asset_id):
        raise RuntimeError("Asset metadata did not confirm the requested ID")
    kind, actual_owner_id = asset_owner(metadata)
    if (kind, actual_owner_id) != (owner_type, str(owner_id)):
        raise RuntimeError(
            f"Asset owner is {kind or 'unknown'} {actual_owner_id or 'unknown'}, "
            f"expected {owner_type} {owner_id}"
        )
    asset_type = metadata.get("assetType")
    if asset_type not in ("Model", "Package", "ASSET_TYPE_MODEL", "ASSET_TYPE_PACKAGE"):
        raise RuntimeError(f"Expected a Model or Package asset, got {asset_type or 'unknown'}")
    versions = api_request(VERSIONS.format(asset_id), cookie)
    if not isinstance(versions.get("assetVersions", versions.get("data")), list):
        raise RuntimeError("Published-version response did not contain a version list")
    return metadata, versions


def version_numbers(versions: dict) -> list[int]:
    numbers = []
    for item in versions.get("assetVersions", versions.get("data", [])):
        if isinstance(item, dict):
            number = item.get("VersionNumber", item.get("versionNumber", item.get("assetVersion")))
            if isinstance(number, dict):
                number = number.get("versionNumber", number.get("revisionId"))
            if isinstance(number, str) and number.isdecimal():
                number = int(number)
            if isinstance(number, int):
                numbers.append(number)
    return sorted(set(numbers), reverse=True)


def latest_version(versions: dict) -> int | None:
    return next(iter(version_numbers(versions)), None)


def receipt_write(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".partial")
    temp.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def publish(asset_id: int, owner_type: str, owner_id: int, package: Path, cookie: str, receipt_dir: Path) -> None:
    data = read_package(package)
    asset_metadata, versions_before = inspect(asset_id, owner_type, owner_id, cookie)
    old_version = latest_version(versions_before)
    metadata = {
        "assetType": asset_metadata["assetType"],
        "assetId": str(asset_id),
        "creationContext": {"creator": {"groupId" if owner_type == "group" else "userId": str(owner_id)}},
    }
    content_type, body = multipart_body(metadata, package.name, data)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_path = receipt_dir / f"package-{asset_id}-{stamp}.json"
    if receipt_path.exists():
        raise RuntimeError(f"Receipt already exists: {receipt_path}")
    receipt = {
        "assetId": asset_id, "ownerType": owner_type, "ownerId": owner_id,
        "package": str(package.resolve()),
        "sha256": hashlib.sha256(data).hexdigest(), "previousVersion": old_version,
        "state": "request_started", "startedUtc": stamp,
    }
    receipt_write(receipt_path, receipt)
    url = f"{BASE}/assets/{asset_id}"
    try:
        try:
            result = api_request(url, cookie, method="PATCH", body=body, content_type=content_type)
        except ApiError as exc:
            if exc.status != 403 or not exc.csrf:
                raise
            # 403 CSRF challenge rejects the first request without creating a version.
            result = api_request(url, cookie, method="PATCH", body=body, content_type=content_type, csrf=exc.csrf)
    except Exception:
        receipt["state"] = "outcome_unknown_check_versions_before_retry"
        receipt_write(receipt_path, receipt)
        raise
    name = result.get("path") or result.get("name") or result.get("operationId")
    match = OPERATION_RE.fullmatch(name) if isinstance(name, str) else None
    if not match:
        receipt["state"] = "response_without_operation_check_versions"
        receipt_write(receipt_path, receipt)
        raise RuntimeError("Upload response had no operation ID; inspect versions before retrying")
    operation_id = match.group(1)
    receipt.update(state="accepted", operationId=operation_id)
    receipt_write(receipt_path, receipt)
    print(f"Update accepted for asset {asset_id}; operation {operation_id}; receipt {receipt_path}")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        operation = api_request(f"{BASE}/operations/{operation_id}", cookie)
        if operation.get("done") or operation.get("response") or operation.get("error"):
            if operation.get("error"):
                receipt["state"] = "rejected"
                receipt_write(receipt_path, receipt)
                raise RuntimeError("Roblox operation rejected the update; see the receipt and account dashboard")
            receipt["state"] = "operation_complete"
            receipt_write(receipt_path, receipt)
            operation_asset_id = (operation.get("response") or {}).get("assetId")
            if operation_asset_id is not None and str(operation_asset_id) != str(asset_id):
                raise RuntimeError("Operation completed for an unexpected asset ID")
            _, versions_after = inspect(asset_id, owner_type, owner_id, cookie)
            new_version = latest_version(versions_after)
            receipt["newVersion"] = new_version
            receipt["state"] = "verified" if old_version is not None and new_version is not None and new_version > old_version else "version_unverified"
            receipt_write(receipt_path, receipt)
            if receipt["state"] != "verified":
                raise RuntimeError("Operation completed, but version advancement was not confirmed; do not resubmit")
            print(f"Verified package {asset_id}: version {old_version} -> {new_version}")
            return
        time.sleep(2)
    raise TimeoutError(f"Operation {operation_id} is still processing; inspect it before retrying")
