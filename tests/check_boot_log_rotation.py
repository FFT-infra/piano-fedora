#!/usr/bin/env python3
"""Offline clock recovery must not collide with an existing daily boot log."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('finalizer', ROOT / 'scripts/finalize-fedora-rootfs.py')
finalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalizer)


def main():
    tool = shutil.which('logrotate')
    if not tool:
        raise RuntimeError('logrotate is required for the collision regression')
    with tempfile.TemporaryDirectory(prefix='boot-log-', dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        finalizer.stage_piano_hardware_fixes(
            root, finalizer.piano_desktop.load('gnome', '44'))
        configuration = root / 'etc/logrotate.d/bootlog'
        assert configuration.is_file(), 'image does not install collision-safe boot log rotation'
        log = root / 'boot.log'
        archive = root / ('boot.log-' + time.strftime('%Y%m%d'))
        archive.write_text('previous boot must survive\n')
        log.write_text('current boot\n')
        config = root / 'logrotate.conf'

        def rotate(body):
            config.write_text('dateext\n' + body.replace('/var/log/boot.log', str(log)))
            config.chmod(0o600)
            return subprocess.run([tool, '--force', '--state', str(root / 'state'), str(config)],
                                  capture_output=True, text=True)

        stock = '/var/log/boot.log\n{\nmissingok\ndaily\ncopytruncate\nrotate 7\nnotifempty\n}\n'
        bad = rotate(stock)
        assert bad.returncode != 0 and 'already exists' in bad.stderr, bad.stdout + bad.stderr
        assert log.read_text() == 'current boot\n'
        good = rotate(configuration.read_text())
        assert good.returncode == 0, good.stdout + good.stderr
        assert archive.read_text() == 'previous boot must survive\n'
        rotated = [p for p in root.glob('boot.log-*') if p != archive]
        assert len(rotated) == 1 and rotated[0].read_text() == 'current boot\n'
        assert log.read_bytes() == b'', 'copytruncate did not preserve the active log inode'
    print('OK: daily-name collision reproduced; timestamped rotation preserves both logs')


if __name__ == '__main__':
    main()
