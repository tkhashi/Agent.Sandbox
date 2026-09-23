#!/usr/bin/env bash
# TypeSafe API (Noul) 疎通確認サンプル
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  echo "エラー: TYPESAFE_API_KEY が設定されていません（.env を確認してください）" >&2
  exit 1
fi

curl -sS -X POST "https://api.typesafe.ai/v1/systemone" \
  -H "Authorization: Bearer ${TYPESAFE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "state": "決済が3日間失敗し続けています。至急対応をお願いします。",
    "model": "jev-latest",
    "questions": {
      "is_urgent": {
        "type": "noul",
        "instructions": "この文章は緊急性を伝えているか？"
      }
    }
  }'
echo
