#!/usr/bin/env bash
#
# Load test curl request for chat.sefaria.org (or any chat API base URL).
#
# Requires: CHATBOT_USER_TOKEN_SECRET (or CHATBOT_USER_ID_SECRET) to generate
# a valid encrypted userId token. For production, use the same secret as the
# deployed chat server.
#
# Usage:
#   CHATBOT_USER_TOKEN_SECRET=your-secret ./curl_loadtest.sh
#   CHATBOT_USER_TOKEN_SECRET=your-secret ./curl_loadtest.sh https://chat.sefaria.org
#   ./curl_loadtest.sh https://chat.sefaria.org "Tell me about Shabbat"
#
# Manual curl (if you have a valid userId token):
#   curl -N -X POST https://chat.sefaria.org/api/v2/chat/stream \
#     -H "Content-Type: application/json" \
#     -d '{"userId":"<TOKEN>","sessionId":"sess-1","messageId":"msg-1","timestamp":"2025-03-11T12:00:00.000Z","text":"What is the Shema?"}'
#
# Default URL: https://chat.sefaria.org (uses /api/v2/chat/stream)

set -e

BASE_URL="${1:-https://chat.sefaria.org}"
CHATBOT_USER_TOKEN_SECRET=Mg57y8NKXyaZCQHFbFvXECUEoWhjGoUXJbbVt3H
USER_ID="cLmwgp3ikvK5B0883-1U6JIhm1ZkQFTuiX19h0PGzRdqfuo9rYCuSd9Fob6oG7Yx5tA4fuhH6SZ18PaE1S13-u8DD0oK7sZfn96Zo8INN0PI8Bfhxco4bA2d66dEJ8ePTCp2NMkbmrRd9Dny9sijiY-eKOAJcDZlNb4JPaSHlO2t9ckKbdn_IhQqPboINA=="
SESSION_ID="$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "sess-$(date +%s)-$$")"
MESSAGE_ID="msg-$(date +%s)-$$"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%S.000Z)"
QUESTION="${2:-What is the Shema?}"

# Generate encrypted userId token (mirrors load_test.py)
TOKEN=$(SECRET="$SECRET" USER_ID="$USER_ID" python3 -c '
import base64, hashlib, json, os
from datetime import datetime, timezone, timedelta

secret, user_id = os.environ["SECRET"], os.environ["USER_ID"]
def _derive_key(s): return hashlib.sha256(s.encode()).digest()

exp = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
payload = json.dumps({"id": user_id, "expiration": exp}).encode()
nonce = os.urandom(12)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
enc = AESGCM(_derive_key(secret)).encrypt(nonce, payload, None)
print(base64.urlsafe_b64encode(nonce + enc).rstrip(b"=").decode(), end="")
')

echo "POST $BASE_URL/api/v2/chat/stream"
echo "---"
export TOKEN SESSION_ID MESSAGE_ID TIMESTAMP QUESTION
PAYLOAD=$(python3 -c '
import json, os
print(json.dumps({
    "userId": os.environ["TOKEN"],
    "sessionId": os.environ["SESSION_ID"],
    "messageId": os.environ["MESSAGE_ID"],
    "timestamp": os.environ["TIMESTAMP"],
    "text": os.environ.get("QUESTION", "What is the Shema?"),
    "isLoadTest": True
}))
')
curl -sS -N -X POST "$BASE_URL/api/v2/chat/stream" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"
