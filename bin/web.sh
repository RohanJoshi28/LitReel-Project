#!/usr/bin/env bash
set -euo pipefail
export WEB_CONCURRENCY=1
exec gunicorn app:app
