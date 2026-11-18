"""Build a reproducible ComfyUI custom-node ZIP without local state or secrets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', '__pycache__', '.pytest_cache', 'output', 'dist', 'state', '.playwright', '.playwright-cli'}
PRIVATE_FILES = {'profiles.json', 'presets.json', 'presets.json.lock', 'tasks.json', 'auth-instance.json', 'container.env'}


def main():
    target = ROOT / 'dist' / 'ComfyUI-EasyAPI-0.1.0.zip'
    target.parent.mkdir(exist_ok=True)
    included = []
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob('*')):
            relative = path.relative_to(ROOT)
            if not path.is_file() or any(part in SKIP or part.endswith('.egg-info') for part in relative.parts):
                continue
            if path.suffix.lower() in {'.pem', '.key', '.pyc', '.p12', '.pfx'} or path.name in PRIVATE_FILES:
                continue
            if path.name == '.env' or path.name.startswith(('.env.', 'container-inspect')):
                continue
            if relative.as_posix() == 'IMPLEMENTATION-CONTRACT.md':
                continue
            name = 'ComfyUI-EasyAPI/' + relative.as_posix()
            item = zipfile.ZipInfo(name, date_time=(2026, 11, 17, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            archive.writestr(item, path.read_bytes())
            included.append(relative.as_posix())
    report = {'filename': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': included}
    (target.parent / 'release.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(target), 'sha256': report['sha256'], 'file_count': len(included)}))


if __name__ == '__main__':
    main()
