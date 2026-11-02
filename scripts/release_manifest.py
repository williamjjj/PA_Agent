"""Archive clean binaries (never local user data) and emit SHA-256 receipts."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
release = root/'release'; release.mkdir(exist_ok=True)
archive = release/'PA-Agent-1.68.1-Portable-x64.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
    for path in sorted((root/'dist/PA-Agent').rglob('*')):
        relative = path.relative_to(root/'dist/PA-Agent')
        if path.is_file() and relative.parts[0] != 'data':
            output.write(path, Path('PA-Agent')/relative)
files=[]
for path in sorted(release.iterdir()):
    if path.suffix not in {'.exe','.zip'}: continue
    files.append({'file':path.name,'bytes':path.stat().st_size,
                  'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()})
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
(release/'manifest.json').write_text(json.dumps({'version':'1.68.1','commit':commit,'files':files},indent=2),encoding='utf-8')
(release/'SHA256SUMS.txt').write_text(''.join(f"{f['sha256']}  {f['file']}\n" for f in files),encoding='utf-8')
print(json.dumps(files,indent=2))
