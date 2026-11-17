import json
from pathlib import Path
import re
import subprocess

info = json.loads(subprocess.check_output(['/opt/homebrew/bin/docker', 'inspect', 'comfytv']))[0]
print(json.dumps({
    'image': info['Config']['Image'], 'image_id': info['Image'],
    'working_dir': info['Config']['WorkingDir'], 'user': info['Config']['User'],
    'entrypoint': info['Config']['Entrypoint'], 'cmd': info['Config']['Cmd'],
    'mounts': info['Mounts'], 'ports': info['HostConfig']['PortBindings'],
    'restart': info['HostConfig']['RestartPolicy'], 'network_mode': info['HostConfig']['NetworkMode'],
    'memory': info['HostConfig']['Memory'], 'shm': info['HostConfig']['ShmSize'],
    'env_names': [item.split('=', 1)[0] for item in info['Config']['Env']],
}, indent=2))
p = Path('/Users/easyapi/AIWorkFlow/deploy/comfytv-host/NEW-DOCKER-RUN.sh')
for line in p.read_text().splitlines():
    if re.search(r'(?i)(api.?key|token|secret|password|passwd|authorization|access.?key)', line):
        print('# [credential-related line omitted]')
    else:
        print(line)
