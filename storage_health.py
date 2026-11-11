"""Check runtime storage before application state is created."""

import os
import tempfile
from pathlib import Path


def _is_mounted_directory(path: Path) -> bool:
    """Check Linux's mount table, including bind mounts."""
    mountinfo = Path('/proc/self/mountinfo')
    try:
        with mountinfo.open(encoding='utf-8') as mounts:
            for line in mounts:
                fields = line.split(' - ', 1)[0].split()
                if len(fields) >= 5:
                    mount_path = fields[4].replace('\\040', ' ').replace('\\011', '\t')
                    if Path(mount_path) == path:
                        return True
    except OSError:
        return False
    return False


def validate_data_root(root: Path) -> Path:
    root = Path(root).resolve()
    on_render = os.getenv('RENDER', '').lower() == 'true'
    is_production = os.getenv('APP_ENV', '').lower() == 'prod'
    has_storage_config = bool(os.getenv('EXAM_DATA_ROOT') or os.getenv('EXAM_DATA_DIR'))
    requirement = os.getenv('REQUIRE_PERSISTENT_DISK', '').lower()
    if requirement and requirement not in ('true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
        raise RuntimeError('REQUIRE_PERSISTENT_DISK must be true or false.')
    require_disk = (on_render and (is_production or has_storage_config)) if not requirement else requirement in ('true', '1', 'yes', 'on')
    if require_disk and not _is_mounted_directory(root):
        raise RuntimeError(f'Persistent disk is not mounted at {root}. Refusing to use ephemeral storage.')
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryFile(dir=root) as test_file:
            test_file.write(b'persistent-storage-write-check')
            test_file.flush()
    except OSError as exc:
        raise RuntimeError(f'Data directory is not writable: {root}') from exc
    return root
