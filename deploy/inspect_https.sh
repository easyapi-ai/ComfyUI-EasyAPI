/opt/homebrew/bin/docker logs --tail 30 comfytv-easyapi-https
/opt/homebrew/bin/docker exec comfytv-easyapi-https wget -q -O - http://comfytv:8188/features
