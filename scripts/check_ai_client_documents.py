"""Release check: fetch real AI clients' CIMD documents and validate them as PathSix would.

ChatGPT and Claude connect by publishing a client ID metadata document. If a
vendor changes that document in a way PathSix rejects, customers see
"invalid_client". Run this before each release (and when a client stops
connecting) to catch that first. Prints only pass/fail and the fields checked.

Usage: python scripts/check_ai_client_documents.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.client_metadata import ClientMetadataError, fetch  # noqa: E402

KNOWN_CLIENTS = {
    'ChatGPT': 'https://chatgpt.com/oauth/client.json',
    'Claude': 'https://claude.ai/oauth/mcp-oauth-client-metadata',
}


def main():
    failures = 0
    for name, client_id in KNOWN_CLIENTS.items():
        try:
            metadata = fetch(client_id)
            print(json.dumps({'client': name, 'result': 'accepted', 'client_id': client_id,
                              'name': metadata['name'], 'redirect_uris': metadata['redirect_uris']}))
        except ClientMetadataError as error:
            failures += 1
            print(json.dumps({'client': name, 'result': 'REJECTED', 'client_id': client_id, 'reason': str(error)}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
