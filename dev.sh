#!/bin/sh
export PATH="/Users/presidente/.nvm/versions/node/v24.17.0/bin:$PATH"
cd "$(dirname "$0")"
exec npm run dev
