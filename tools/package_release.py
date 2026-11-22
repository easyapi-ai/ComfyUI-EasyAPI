"""Build a reproducible ComfyUI custom-node ZIP without local state or secrets."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', '__pycache__', '.pytest_cache', 'output', 'dist', 'state', '.playwright', '.playwright-cli'}
PRIVATE_FILES = {'profiles.json', 'presets.json', 'presets.json.lock', 'tasks.json', 'auth-instance.json', 'container.env'}
PUBLIC_INSTALL_FILES = ('LICENSE', '__init__.py', 'requirements.txt', 'pyproject.toml', 'easyapi_comfy', 'locales', 'web')
PUBLIC_README = """# ComfyUI-EasyAPI

在 ComfyUI 工作流中使用 EasyAPI 的图像、视频、对话和语音模型。

1. 将 `ComfyUI-EasyAPI` 目录放入 `ComfyUI/custom_nodes/`。
2. 在 ComfyUI 根目录使用它实际运行的 Python 安装依赖：

   ```shell
   python -m pip install -r custom_nodes/ComfyUI-EasyAPI/requirements.txt
   ```

3. 重启 ComfyUI，从侧栏的 EasyAPI 连接管理添加 API Key。官方网关为 `https://token.easyapi.com`。

[安装指南和示例工作流](https://www.easyapi.com/integrations/comfyui) · [English guide and image workflow](https://www.easyapi.com/en/integrations/comfyui)
"""


def package_public_release(ref: str, output: Path) -> None:
    """Publish only the installable files from a committed revision."""
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', f'{ref}^{{commit}}'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', '--format=zip', commit, '--', *PUBLIC_INSTALL_FILES], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        version = tomllib.loads(source.read('pyproject.toml').decode())['project']['version']
        output.mkdir(parents=True, exist_ok=True)
        target = output / f'ComfyUI-EasyAPI-{version}.zip'
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as release:
            files = [name for name in source.namelist() if not name.endswith('/')]
            for name in sorted([*files, 'README.md']):
                item = zipfile.ZipInfo(f'ComfyUI-EasyAPI/{name}')
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o644 << 16
                content = PUBLIC_README.encode() if name == 'README.md' else source.read(name)
                release.writestr(item, content)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.sha256').write_text(f'{digest}  {target.name}\n', encoding='ascii', newline='\n')
    print(json.dumps({'file': str(target), 'commit': commit, 'sha256': digest}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-ref', help='Package only the public installation files from this Git revision')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if args.public_ref:
        package_public_release(args.public_ref, args.output)
        return
    target = args.output / 'ComfyUI-EasyAPI-0.1.0.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
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
