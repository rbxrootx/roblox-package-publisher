"""Create and version Roblox model Packages from a terminal."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from asset_create import await_asset_id, create_asset, read_package
from package_version import ApiError, auth_cookie, inspect, latest_version, publish, version_numbers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a new Package asset (Open Cloud API key)")
    create.add_argument("package", type=Path)
    create_owner = create.add_mutually_exclusive_group(required=True)
    create_owner.add_argument("--group-id", type=int)
    create_owner.add_argument("--user-id", type=int)
    create.add_argument("--name", required=True)
    create.add_argument("--description", default="")

    inspect_cmd = commands.add_parser("inspect", help="Check owner and latest published version")
    inspect_cmd.add_argument("asset_id", type=int)
    inspect_owner = inspect_cmd.add_mutually_exclusive_group(required=True)
    inspect_owner.add_argument("--group-id", type=int)
    inspect_owner.add_argument("--user-id", type=int)

    versions_cmd = commands.add_parser("versions", help="List published version numbers")
    versions_cmd.add_argument("asset_id", type=int)
    versions_owner = versions_cmd.add_mutually_exclusive_group(required=True)
    versions_owner.add_argument("--group-id", type=int)
    versions_owner.add_argument("--user-id", type=int)

    update = commands.add_parser("update", help="Publish a new version on the same asset ID")
    update.add_argument("asset_id", type=int)
    update.add_argument("package", type=Path)
    update_owner = update.add_mutually_exclusive_group(required=True)
    update_owner.add_argument("--group-id", type=int)
    update_owner.add_argument("--user-id", type=int)
    update.add_argument("--receipt-dir", type=Path, default=Path("receipts"))

    args = parser.parse_args()
    owner_type = "group" if args.group_id is not None else "user"
    owner_id = args.group_id if args.group_id is not None else args.user_id
    if owner_id <= 0 or getattr(args, "asset_id", 1) <= 0:
        parser.error("asset and group IDs must be positive")
    try:
        if args.command == "create":
            data = read_package(args.package)
            key = os.environ.get("ROBLOX_API_KEY")
            if not key:
                raise RuntimeError("Set ROBLOX_API_KEY in your local environment")
            operation_id = create_asset(args.package, data, key, "groupId" if owner_type == "group" else "userId", owner_id, args.name, args.description)
            print(f"Create accepted; operation {operation_id}")
            asset_id = await_asset_id(operation_id, key)
            print(f"Package ID: {asset_id}\nrbxassetid://{asset_id}")
        elif args.command in ("inspect", "versions"):
            metadata, versions = inspect(args.asset_id, owner_type, owner_id, auth_cookie())
            if args.command == "versions":
                print(json.dumps({"assetId": args.asset_id, "versions": version_numbers(versions)}))
            else:
                print(json.dumps({
                    "assetId": args.asset_id,
                    "owner": {"type": owner_type, "id": owner_id},
                    "name": metadata.get("displayName"),
                    "latestVersion": latest_version(versions),
                }))
        else:
            publish(args.asset_id, owner_type, owner_id, args.package, auth_cookie(), args.receipt_dir)
    except (ApiError, RuntimeError, ValueError, OSError, TimeoutError) as exc:
        print(f"rbxpackage: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
