#!/bin/bash
set -euo pipefail
python3 /app/network.py
exec sleep infinity
