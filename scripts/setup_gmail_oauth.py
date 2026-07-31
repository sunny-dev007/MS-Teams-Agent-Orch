"""
One-time script to obtain Gmail OAuth2 refresh token.

Prerequisites:
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable the Gmail API
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download the credentials JSON file

Usage:
    python scripts/setup_gmail_oauth.py --credentials path/to/credentials.json
"""

import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def main():
    parser = argparse.ArgumentParser(description="Set up Gmail OAuth2")
    parser.add_argument("--credentials", required=True, help="Path to OAuth credentials JSON")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.credentials, SCOPES)
    creds = flow.run_local_server(port=8090)

    print("\n" + "=" * 60)
    print("OAuth setup complete! Add these to your .env file:")
    print("=" * 60)

    with open(args.credentials) as f:
        client_config = json.load(f)
        installed = client_config.get("installed", client_config.get("web", {}))

    print(f"GOOGLE_CLIENT_ID={installed['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={installed['client_secret']}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
