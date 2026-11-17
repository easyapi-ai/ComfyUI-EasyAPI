/opt/homebrew/bin/docker inspect comfytv --format '{{json .State}}'
/opt/homebrew/bin/docker logs --tail 70 comfytv 2>&1 | sed -E 's/sk-[A-Za-z0-9_-]+/[redacted]/g'
