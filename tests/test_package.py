"""Offline checks for ownership guards and the multipart version request."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import package_version as uploader  # noqa: E402
import asset_create  # noqa: E402


ASSET = 1234567890
GROUP = 1234567
META = {
    "path": f"assets/{ASSET}",
    "assetType": "Model",
    "creationContext": {"creator": {"groupId": str(GROUP)}},
}


class PackageTests(unittest.TestCase):
    def test_versions_accept_native_asset_version_shape(self) -> None:
        self.assertEqual(
            uploader.version_numbers({"assetVersions": [{"assetVersion": "3"}, {"assetVersion": 2}]}),
            [3, 2],
        )

    def test_create_uses_group_owner_and_rbxm_multipart(self) -> None:
        with patch.object(asset_create, "request_json", return_value={"path": "operations/new_package"}) as request:
            operation = asset_create.create_asset(
                Path("cutscene.rbxm"), b"<roblox!\x00test", "key", "groupId", GROUP,
                "Demo Cutscene", "Cutscene",
            )
        self.assertEqual(operation, "new_package")
        args, kwargs = request.call_args
        self.assertEqual(args[0], asset_create.ASSETS_URL)
        body = kwargs["body"]
        self.assertIn(b'"groupId":"1234567"', body)
        self.assertIn(b'"assetType":"Model"', body)
        self.assertIn(b"Content-Type: model/x-rbxm", body)
        self.assertIn(b"<roblox!\x00test", body)

    def test_owner_check_fails_closed(self) -> None:
        wrong = {**META, "creationContext": {"creator": {"groupId": "1"}}}
        with patch.object(uploader, "api_request", return_value=wrong) as request:
            with self.assertRaisesRegex(RuntimeError, "Asset owner"):
                uploader.inspect(ASSET, "group", GROUP, "cookie")
        self.assertEqual(request.call_count, 1)

    def test_update_retries_csrf_and_verifies_same_asset_version(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            package = Path(directory) / "cutscene.rbxm"
            package.write_bytes(b"<roblox!\x00test")
            calls = []

            def fake_request(url, cookie, **kwargs):
                calls.append((url, kwargs))
                if url.endswith("/versions"):
                    number = 2 if any(call[1].get("csrf") for call in calls) else 1
                    return {"assetVersions": [{"assetVersion": number}]}
                if url.endswith(f"/assets/{ASSET}") and kwargs.get("method") == "PATCH":
                    if not kwargs.get("csrf"):
                        raise uploader.ApiError(403, "test-csrf")
                    body = kwargs["body"]
                    self.assertIn(b'"assetId":"1234567890"', body)
                    self.assertIn(b'"groupId":"1234567"', body)
                    self.assertIn(b"Content-Type: model/x-rbxm", body)
                    self.assertIn(b"<roblox!\x00test", body)
                    return {"operationId": "op_test"}
                if url.endswith("/operations/op_test"):
                    return {"response": {"assetId": str(ASSET)}}
                return META

            with patch.object(uploader, "api_request", side_effect=fake_request):
                uploader.publish(ASSET, "group", GROUP, package, "cookie", Path(directory) / "receipts")
            receipt = json.loads(next((Path(directory) / "receipts").glob("*.json")).read_text())
            self.assertEqual(receipt["state"], "verified")
            self.assertEqual((receipt["previousVersion"], receipt["newVersion"]), (1, 2))
            patches = [kwargs for _, kwargs in calls if kwargs.get("method") == "PATCH"]
            self.assertEqual(len(patches), 2)
            self.assertIsNone(patches[0].get("csrf"))
            self.assertEqual(patches[1]["csrf"], "test-csrf")

    def test_unknown_outcome_preserves_receipt(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            package = Path(directory) / "cutscene.rbxm"
            package.write_bytes(b"<roblox!\x00test")

            def fake_request(url, cookie, **kwargs):
                if url.endswith("/versions"):
                    return {"assetVersions": [{"assetVersion": 1}]}
                if kwargs.get("method") == "PATCH":
                    raise RuntimeError("unknown network outcome")
                return META

            with patch.object(uploader, "api_request", side_effect=fake_request):
                with self.assertRaisesRegex(RuntimeError, "unknown network outcome"):
                    uploader.publish(ASSET, "group", GROUP, package, "cookie", Path(directory) / "receipts")
            receipt = json.loads(next((Path(directory) / "receipts").glob("*.json")).read_text())
            self.assertEqual(receipt["state"], "outcome_unknown_check_versions_before_retry")


if __name__ == "__main__":
    unittest.main()
