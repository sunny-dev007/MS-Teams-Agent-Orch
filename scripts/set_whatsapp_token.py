#!/usr/bin/env python3
"""Update WhatsApp access token in local .env and/or Azure App Service.

Why this exists
---------------
The "Generate access token" button in Meta Developer Console creates a temporary
token that expires in ~24 hours. That is why WhatsApp replies stop working daily.

For production, create a **permanent System User token** in Meta Business Manager:

1. Open https://business.facebook.com/settings/system-users
2. Add a System User (role: Admin) if you do not already have one
3. Assign Assets → your App (Full control) + WhatsApp Business Account (Full control)
4. Generate Token → select your app → permissions:
     - whatsapp_business_messaging
     - whatsapp_business_management
     - business_management
   Set token expiry to **Never** (or 60/90 days if Never is unavailable)
5. Copy the token once (Meta will not show it again)

Then run:

  python scripts/set_whatsapp_token.py --token 'EAAB...' --azure \\
      --resource-group ai-agent-rg --app-name whatsapp-ai-agent-sunny

Or update only local .env:

  python scripts/set_whatsapp_token.py --token 'EAAB...' --env-file .env

Verify after deploy:

  curl 'https://whatsapp-ai-agent-sunny.azurewebsites.net/health?deep=1'
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _update_env_file(env_path: Path, token: str) -> None:
    text = env_path.read_text() if env_path.exists() else ""
    pattern = re.compile(r"^WHATSAPP_ACCESS_TOKEN=.*$", re.MULTILINE)
    line = f"WHATSAPP_ACCESS_TOKEN={token}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    env_path.write_text(text)
    print(f"Updated {env_path}")


def _update_azure(resource_group: str, app_name: str, token: str) -> None:
    cmd = [
        "az",
        "webapp",
        "config",
        "appsettings",
        "set",
        "--resource-group",
        resource_group,
        "--name",
        app_name,
        "--settings",
        f"WHATSAPP_ACCESS_TOKEN={token}",
        "-o",
        "none",
    ]
    subprocess.check_call(cmd)
    print(f"Updated Azure App Setting WHATSAPP_ACCESS_TOKEN on {app_name}")
    print("Restarting app so the new token is loaded...")
    subprocess.check_call(
        [
            "az",
            "webapp",
            "restart",
            "--resource-group",
            resource_group,
            "--name",
            app_name,
            "-o",
            "none",
        ]
    )
    print("Done. Check: curl 'https://{}.azurewebsites.net/health?deep=1'".format(app_name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--token", required=True, help="Permanent System User access token from Meta")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Local .env path")
    parser.add_argument("--azure", action="store_true", help="Also push token to Azure App Settings")
    parser.add_argument("--resource-group", default="ai-agent-rg")
    parser.add_argument("--app-name", default="whatsapp-ai-agent-sunny")
    parser.add_argument("--skip-env", action="store_true", help="Do not write local .env")
    args = parser.parse_args()

    token = args.token.strip()
    if len(token) < 50:
        print("Token looks too short — paste the full Meta access token.", file=sys.stderr)
        return 1

    if not args.skip_env:
        _update_env_file(args.env_file, token)

    if args.azure:
        _update_azure(args.resource_group, args.app_name, token)
    elif args.skip_env:
        print("Nothing to do: pass --azure and/or omit --skip-env", file=sys.stderr)
        return 1

    print(
        "\nReminder: do NOT use the temporary token from developers.facebook.com "
        "API Setup. Use a Business Manager System User token with Never expiry."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
