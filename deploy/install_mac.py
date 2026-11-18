"""Install a verified release while preserving the running container for rollback.

Executed over SSH on the authorized Mac host. Never prints environment values.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import zipfile

DOCKER = '/opt/homebrew/bin/docker'
BASE = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-easyapi')
HOST = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-host')
PLUGIN = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-cloud-nodes/ComfyUI-EasyAPI')
ARCHIVE = Path('/tmp/ComfyUI-EasyAPI-0.1.0.zip')
REPORT = Path('/tmp/comfyui-easyapi-release.json')
NETWORK = 'comfytv-easyapi'


def run(*args, **kwargs):
    return subprocess.run([DOCKER, *args], check=True, **kwargs)


def inspect(name):
    return json.loads(subprocess.check_output([DOCKER, 'inspect', name]))[0]


def write_private(path, text):
    path.write_text(text)
    path.chmod(0o600)


release = json.loads(REPORT.read_text())
sha = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
if sha != release['sha256']:
    raise RuntimeError('Release checksum mismatch')
BASE.mkdir(exist_ok=True, mode=0o700)
version = BASE / 'releases' / sha[:16]
version.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(ARCHIVE) as archive:
    for item in archive.infolist():
        path = (version / item.filename).resolve()
        if version.resolve() not in path.parents:
            raise RuntimeError('Unsafe archive path')
    archive.extractall(version)
source = version / 'ComfyUI-EasyAPI'
if not (source / '__init__.py').is_file():
    raise RuntimeError('Incomplete node package')
current = inspect('comfytv')
queue = json.load(urllib.request.urlopen('http://127.0.0.1:3106/queue', timeout=10))
if queue['queue_running'] or queue['queue_pending']:
    raise RuntimeError('ComfyUI queue must be idle before installing')
stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
backup = BASE / ('backup-' + stamp)
backup.mkdir(mode=0o700)
write_private(backup / 'container-inspect.json', json.dumps(current, indent=2))
shutil.copy2(HOST / 'NEW-DOCKER-RUN.sh', backup / 'NEW-DOCKER-RUN.sh')
(backup / 'NEW-DOCKER-RUN.sh').chmod(0o600)
baseline = 'comfytv-comfyui-easyapi:baseline-' + stamp
# Manager can have updated the core in the writable container layer. Preserve it.
previous_install = BASE / 'installation.json'
if previous_install.exists():
    previous = json.loads(previous_install.read_text())
    shutil.copy2(previous_install, backup / 'installation.previous.json')
    baseline = previous['baseline_image']
else:
    run('commit', '--change', 'ENTRYPOINT []', 'comfytv', baseline)
dockerfile = version / 'Dockerfile'
dockerfile.write_text(f'FROM {baseline}\nCOPY ComfyUI-EasyAPI/requirements.txt /tmp/easyapi-requirements.txt\nRUN python -m pip install --no-cache-dir -r /tmp/easyapi-requirements.txt\n')
image = 'comfytv-comfyui-easyapi:' + sha[:16]
run('build', '-t', image, '-f', str(dockerfile), str(version))
if PLUGIN.is_symlink():
    PLUGIN.unlink()
elif PLUGIN.exists():
    raise RuntimeError('Existing unmanaged plugin directory; inspect it before replacing')
PLUGIN.symlink_to(source, target_is_directory=True)

mock = subprocess.run([DOCKER, 'inspect', 'comfytv-easyapi-mock'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
if mock.returncode == 0:
    run('rm', '-f', 'comfytv-easyapi-mock')
run('run', '-d', '--name', 'comfytv-easyapi-mock', '--restart', 'unless-stopped', '--network', NETWORK,
    '--mount', f'type=bind,source={source},target=/plugin,readonly', image,
    'python', '/plugin/tools/mock_gateway.py', '--host', '0.0.0.0', '--port', '3199')

env = [item for item in current['Config']['Env'] if not item.startswith(('EASYAPI_STATE_DIR=', 'EASYAPI_PUBLIC_ORIGIN='))]
env += ['EASYAPI_STATE_DIR=/app/user/easyapi', 'EASYAPI_PUBLIC_ORIGIN=https://192.168.2.48:3443']
env_file = BASE / 'container.env'
write_private(env_file, '\n'.join(env) + '\n')
args = ['create', '--name', 'comfytv', '--restart', current['HostConfig']['RestartPolicy']['Name'] or 'no',
        '--env-file', str(env_file), '--shm-size', str(current['HostConfig']['ShmSize'])]
for port, bindings in current['HostConfig']['PortBindings'].items():
    for binding in bindings:
        prefix = (binding['HostIp'] + ':') if binding.get('HostIp') else ''
        args += ['-p', prefix + binding['HostPort'] + ':' + port]
for mount in current['Mounts']:
    if mount['Destination'] == '/app/custom_nodes/ComfyUI-EasyAPI':
        continue
    value = f"type=bind,source={mount['Source']},target={mount['Destination']}"
    if not mount['RW']:
        value += ',readonly'
    args += ['--mount', value]
args += ['--mount', f'type=bind,source={PLUGIN},target=/app/custom_nodes/ComfyUI-EasyAPI,readonly']
if current['Config']['WorkingDir']:
    args += ['-w', current['Config']['WorkingDir']]
if current['Config']['User']:
    args += ['--user', current['Config']['User']]
for name, value in (current['Config'].get('Labels') or {}).items():
    args += ['--label', name + '=' + value]
args += [image, *current['Config']['Cmd']]
old = 'comfytv-before-easyapi-' + stamp
run('stop', '--time', '20', 'comfytv')
run('network', 'disconnect', NETWORK, 'comfytv')
run('rename', 'comfytv', old)
try:
    run(*args)
    run('network', 'connect', NETWORK, 'comfytv')
    run('start', 'comfytv')
    healthy = False
    for _ in range(120):
        try:
            with urllib.request.urlopen('http://127.0.0.1:3106/object_info', timeout=3) as response:
                nodes = json.load(response)
                easyapi_nodes = [name for name in nodes if name.startswith('EasyAPI')]
                if len(easyapi_nodes) == 14:
                    healthy = True
                    break
        except Exception:
            pass
        time.sleep(1)
    if not healthy:
        raise RuntimeError('EasyAPI nodes did not become available')
except Exception:
    with (backup / 'failed-startup.log').open('wb') as log:
        subprocess.run([DOCKER, 'logs', 'comfytv'], stdout=log, stderr=subprocess.STDOUT)
    (backup / 'failed-startup.log').chmod(0o600)
    subprocess.run([DOCKER, 'rm', '-f', 'comfytv'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run('rename', old, 'comfytv')
    run('network', 'connect', NETWORK, 'comfytv')
    run('start', 'comfytv')
    raise

launch_script = '''#!/usr/bin/env bash
set -euo pipefail
ROOT=/Users/easyapi/AIWorkFlow/deploy/comfytv-easyapi
# This install preserves the previous container. Full configuration is in
# installation.json; credentials remain in the private container.env file.
docker start comfytv-easyapi-mock comfytv-easyapi-https comfytv
'''
(BASE / 'start.sh').write_text(launch_script)
(BASE / 'start.sh').chmod(0o700)
record = {'release_sha256': sha, 'source': str(source), 'plugin_mount': str(PLUGIN), 'image': image,
          'image_id': inspect('comfytv')['Image'], 'baseline_image': baseline, 'rollback_container': old,
          'backup': str(backup), 'url': 'https://192.168.2.48:3443', 'http_url': 'http://192.168.2.48:3106',
          'easyapi_nodes': easyapi_nodes, 'installed_at': time.time()}
write_private(BASE / 'installation.json', json.dumps(record, indent=2))
# Keep the host's existing rebuild entrypoint aware of the plugin and exact image.
original = (HOST / 'NEW-DOCKER-RUN.sh').read_text()
if '/app/custom_nodes/ComfyUI-EasyAPI' not in original:
    original = original.replace('  -w /app \\\n', f'  -v {PLUGIN}:/app/custom_nodes/ComfyUI-EasyAPI:ro \\\n  --network {NETWORK} \\\n  -e EASYAPI_STATE_DIR=/app/user/easyapi \\\n  -e EASYAPI_PUBLIC_ORIGIN=https://192.168.2.48:3443 \\\n  -w /app \\\n')
original = '\n'.join('IMAGE="${IMAGE:-' + image + '}"' if line.startswith('IMAGE="${IMAGE:-') else line
                     for line in original.splitlines()) + '\n'
(HOST / 'NEW-DOCKER-RUN.sh').write_text(original)
(HOST / 'NEW-DOCKER-RUN.sh').chmod(0o700)
print(json.dumps(record, ensure_ascii=False))
