# Roblox Package Publisher

Create and update Roblox `.rbxm` Packages from the command line. Python 3.10+; no dependencies and no Studio plugin.

## Create

Create an [Open Cloud API key](https://create.roblox.com/docs/cloud/auth/api-keys) with Assets write access for your group, then set it locally as `ROBLOX_API_KEY`.

```sh
python rbxpackage.py create cutscene.rbxm --group-id 1234567 --name "Demo Cutscene"
```

The command prints the new asset ID. Save that ID for updates.
Use `--user-id` instead of `--group-id` for a personal asset.

## Update the same ID

Set `RBX_COOKIE` to your own `.ROBLOSECURITY` cookie in your local environment. Keep it private; never paste it into a command, issue, log, or commit.

```sh
python rbxpackage.py inspect 1234567890 --group-id 1234567
python rbxpackage.py versions 1234567890 --group-id 1234567
python rbxpackage.py update 1234567890 cutscene.rbxm --group-id 1234567
```

`inspect` checks the asset's owner and latest version. `update` confirms the same owner, uploads a new version, polls Roblox, and checks that the version number advanced. It writes a nonsecret receipt in `receipts/`. If a request times out, inspect the asset and receipt before retrying so you do not publish twice.

Creation uses Roblox's documented Open Cloud API. `.rbxm` content updates currently require Roblox's cookie-auth asset endpoint; the API-key endpoint [only accepts `.fbx` updates](https://create.roblox.com/docs/cloud/guides/usage-assets). The cookie-auth route is used by Roblox tooling but is subject to change. This tool has created an `.rbxm` asset; a same-ID update still needs live validation.

Run offline tests with `python -m unittest discover -s tests -v`.
