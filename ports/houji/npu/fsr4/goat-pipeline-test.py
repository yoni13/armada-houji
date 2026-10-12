"""Install or restore the Goat pipelined NPU test. Game-local and reversible."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import time

home = Path.home()
game = home/'.local/share/Steam/steamapps/common/Goat Simulator 3/Goat2/Binaries/Win64'
config = home/'.local/share/Steam/steamapps/compatdata/850190/pfx/drive_c/users/steamuser/AppData/Local/Goat2/Saved/Config/WindowsNoEditor'
state = home/'.local/state/houji-game-debug'
staged = home/'.local/opt/fsr4-htp/goat-pipeline'
probe = sorted(state.glob('goat-fsr4-probe-*'))[-1]
archive = sorted(probe.glob('disabled-experiment-*'))[-1]
sources = {'dxgi.dll': archive/'dxgi.dll', 'ReShade.ini': archive/'ReShade.ini',
           'Houji-TAA.addon': staged/'Houji-TAA.addon', '_nvngx.dll': staged/'_nvngx.dll'}
created_logs = ('ReShade.log', 'houji-ngx-probe.log')


def goat_running():
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if 'goat' in (p/'comm').read_text().lower():
                return True
        except OSError:
            pass
    return False


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


if goat_running():
    raise SystemExit('Goat still running')
mode = sys.argv[1] if len(sys.argv) > 1 else ''
if mode == 'install':
    backup = state/('goat-fsr4-pipeline-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    backup.mkdir(mode=0o700)
    records = []
    for name, source in sources.items():
        dest = game/name
        if dest.is_symlink():
            raise SystemExit('Refusing symlink '+name)
        existed = dest.exists()
        if existed:
            shutil.copy2(dest, backup/name)
        shutil.copy2(source, dest)
        records.append({'path': name, 'existed': existed, 'installed_sha256': digest(dest)})
    engine = config/'Engine.ini'
    engine_existed = engine.exists()
    if engine_existed:
        shutil.copy2(engine, backup/'Engine.ini')
    shutil.copy2(probe/'experimental-Engine.ini', engine)
    (backup/'manifest.json').write_text(json.dumps({
        'game': str(game), 'launch_options': '/usr/libexec/armada/armada-game-launch %command%',
        'engine_ini_existed': engine_existed, 'files': records}, indent=2)+'\n')
    print('Installed pipelined test files:', ', '.join(sources), '+ Engine.ini; backup', backup.name)
elif mode == 'restore':
    backup = sorted(state.glob('goat-fsr4-pipeline-*'))[-1]
    manifest = json.loads((backup/'manifest.json').read_text())
    removed = backup/('removed-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    removed.mkdir(mode=0o700)
    for record in manifest['files']:
        name = Path(record['path']).name
        dest = game/name
        if dest.is_file():
            shutil.move(dest, removed/name)
        if record['existed']:
            shutil.copy2(backup/name, dest)
    for name in created_logs:
        if (game/name).is_file():
            shutil.move(game/name, removed/name)
    engine = config/'Engine.ini'
    if engine.is_file():
        shutil.move(engine, removed/'Engine.ini')
    if manifest['engine_ini_existed']:
        shutil.copy2(backup/'Engine.ini', engine)
    left = [n for n in sources if (game/n).exists() and not any(r['existed'] for r in manifest['files'] if r['path'] == n)]
    if left:
        raise SystemExit('Experiment files still present: '+', '.join(left))
    print('Restored Goat; Engine.ini present:', engine.exists(), '; launch options to restore:', manifest['launch_options'])
else:
    raise SystemExit('usage: goat-pipeline-test.py install|restore')
