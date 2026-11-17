"""Provision the authorized LAN-only HTTPS endpoint on the Mac Docker host."""
import json
import os
from pathlib import Path
import subprocess
import time

DOCKER = '/opt/homebrew/bin/docker'
BASE = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-easyapi')
directory = BASE / 'https'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
(directory / 'data').mkdir(exist_ok=True, mode=0o700)
(directory / 'config').mkdir(exist_ok=True, mode=0o700)
config = '''{
    auto_https disable_redirects
    default_sni 192.168.2.48
}
https://192.168.2.48 {
    tls internal
    reverse_proxy comfytv:8188
}
'''
(directory / 'Caddyfile').write_text(config)
network = 'comfytv-easyapi'
if subprocess.run([DOCKER, 'network', 'inspect', network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
    subprocess.run([DOCKER, 'network', 'create', network], check=True)
current = json.loads(subprocess.check_output([DOCKER, 'inspect', 'comfytv']))[0]
if network not in current['NetworkSettings']['Networks']:
    subprocess.run([DOCKER, 'network', 'connect', network, 'comfytv'], check=True)
image = 'caddy:2-alpine'
if subprocess.run([DOCKER, 'image', 'inspect', image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
    subprocess.run([DOCKER, 'pull', image], check=True)
existing = subprocess.run([DOCKER, 'inspect', 'comfytv-easyapi-https'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
if existing.returncode == 0 and json.loads(existing.stdout)[0]['State']['Status'] == 'created':
    subprocess.run([DOCKER, 'rm', 'comfytv-easyapi-https'], check=True)
    existing = subprocess.CompletedProcess(existing.args, 1)
if existing.returncode == 0:
    subprocess.run([DOCKER, 'exec', 'comfytv-easyapi-https', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile'], check=True)
else:
    subprocess.run([DOCKER, 'run', '-d', '--name', 'comfytv-easyapi-https', '--restart', 'unless-stopped', '--network', network,
        '-p', '3443:443',
        '--mount', f'type=bind,source={directory / "Caddyfile"},target=/etc/caddy/Caddyfile,readonly',
        '--mount', f'type=bind,source={directory / "data"},target=/data',
        '--mount', f'type=bind,source={directory / "config"},target=/config', image], check=True)
root = directory / 'data/caddy/pki/authorities/local/root.crt'
for _ in range(60):
    if root.is_file():
        break
    time.sleep(1)
else:
    raise RuntimeError('Caddy CA certificate was not generated')
details = json.loads(subprocess.check_output([DOCKER, 'image', 'inspect', image]))[0]
record = {'url': 'https://192.168.2.48:3443', 'image': image, 'image_id': details['Id'],
          'repo_digests': details.get('RepoDigests', []), 'root_certificate': str(root), 'installed_at': time.time()}
(directory / 'installation.json').write_text(json.dumps(record, indent=2))
print(json.dumps(record))
