#!/usr/bin/env python3
"""
Google OAuth Token Generator

Run this script once to get a refresh token with all required scopes
(Calendar, Gmail, Tasks).

Usage:
    python scripts/get_token.py

Follow the prompts:
1. Click the generated URL
2. Authorize on Google
3. Copy the 'code' parameter from the failed redirect URL
4. Paste it into the terminal
5. Copy the refresh token to your .env file
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path so we can import from app
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from app.auth.google import ALL_SCOPES, GoogleOAuth

# Load environment variables
load_dotenv()


async def main():
    print("=" * 60)
    print("Google OAuth Token Generator")
    print("=" * 60)
    print()
    print("This will generate a refresh token with the following scopes:")
    for scope in ALL_SCOPES:
        print(f"  - {scope}")
    print()

    # Create OAuth instance with all scopes
    oauth = GoogleOAuth(scopes=ALL_SCOPES)

    # Generate auth URL
    auth_url = oauth.get_auth_url(state="token-generator")

    print("Step 1: Visit this URL in your browser:")
    print()
    print(auth_url)
    print()
    print("Step 2: After authorizing, Google will try to redirect to:")
    print("  http://localhost:8080/?code=...")
    print()
    print("The page will fail to load (that's expected).")
    print()
    print("Step 3: Copy the ENTIRE URL from your browser's address bar.")
    print("It should look like:")
    print("  http://localhost:8080/?code=4/0A...&scope=https://...")
    print()
    print("Paste it here, or just paste the 'code' value:")
    print()

    # Get code from user
    user_input = input("Paste here: ").strip()

    # Extract code if they pasted the full URL
    if "code=" in user_input:
        # Extract code parameter from URL
        code = user_input.split("code=")[1].split("&")[0]
    else:
        code = user_input

    print()
    print("Exchanging code for tokens...")

    try:
        token_data = await oauth.exchange_code(code)

        print()
        print("=" * 60)
        print("SUCCESS! Here's your refresh token:")
        print("=" * 60)
        print()
        print(token_data.refresh_token)
        print()
        print("=" * 60)
        print()
        print("Copy the token above and add it to your .env file:")
        print("  GOOGLE_REFRESH_TOKEN=<paste-token-here>")
        print()
        print("For GCP Cloud Run, update the secret in Secret Manager.")
        print()

    except Exception as e:
        print()
        print("ERROR:", str(e))
        print()
        print("Make sure you:")
        print("  1. Copied the entire code value")
        print("  2. Have GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env")
        print("  3. Set the correct redirect URI in Google Cloud Console")
        print("     (should be http://localhost:8080)")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
