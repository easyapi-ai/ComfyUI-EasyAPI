"""Apply a development package to the mounted test plugin, preserving a backup."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import zipfile

root = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-easyapi')
source = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-cloud-nodes/ComfyUI-EasyAPI').resolve()
if (root / 'releases').resolve() not in source.parents:
    raise RuntimeError('Unexpected plugin source path')
archive = Path('/tmp/ComfyUI-EasyAPI-0.1.0.zip')
report = json.loads(Path('/tmp/comfyui-easyapi-release.json').read_text())
if hashlib.sha256(archive.read_bytes()).hexdigest() != report['sha256']:
    raise RuntimeError('Release checksum mismatch')
queue = json.load(urllib.request.urlopen('http://127.0.0.1:3106/queue', timeout=10))
if queue['queue_running'] or queue['queue_pending']:
    raise RuntimeError('Queue must be idle')
backup = root / ('source-backup-' + str(int(time.time())))
shutil.copytree(source, backup, ignore=shutil.ignore_patterns('__pycache__'))
with zipfile.ZipFile(archive) as release:
    for member in release.infolist():
        parts = Path(member.filename).parts
        if not parts or parts[0] != 'ComfyUI-EasyAPI' or member.is_dir():
            continue
        target = source.joinpath(*parts[1:]).resolve()
        if source not in target.parents:
            raise RuntimeError('Unsafe archive path')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(release.read(member))
subprocess.run(['/opt/homebrew/bin/docker', 'restart', 'comfytv-easyapi-mock', 'comfytv'], check=True)
for _ in range(90):
    try:
        definitions=json.load(urllib.request.urlopen('http://127.0.0.1:3106/object_info', timeout=2))
        nodes=[name for name in definitions if name.startswith('EasyAPI')]
        if len(nodes) == 14:
            print(json.dumps({'development_sha256':report['sha256'],'nodes':len(nodes),'backup':str(backup)}))
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise RuntimeError('Updated nodes did not become available; inspect startup log and source backup')
