"""Seed bundled REV2 references on first start, after the disk is mounted."""
from pathlib import Path
import json
import os
import shutil
import records
from storage_health import validate_data_root


def initialize_data(data_root=None):
    records.load_env()
    root=Path(data_root or os.getenv('EXAM_DATA_ROOT') or os.getenv('EXAM_DATA_DIR') or records.ROOT/'data')
    root=validate_data_root(root)
    destination=root/'references'
    destination.mkdir(exist_ok=True)
    index=destination/'index.json'
    # Existing subject changes/deletions are authoritative; never reset them
    # to bundled defaults on a restart or redeploy.
    if index.exists():
        validate_reference_index(index)
        return root
    source=Path(__file__).resolve().parent/'data'/'references'
    rows=json.loads((source/'index.json').read_text(encoding='utf-8'))
    for row in rows:
        name=Path(row['file']).name
        if name!=row['file']: raise RuntimeError('Invalid bundled reference filename.')
        target=destination/name
        if not target.exists(): shutil.copyfile(source/name,target)
    temporary=destination/'index.seed.tmp'
    temporary.write_text(json.dumps(rows,indent=2),encoding='utf-8')
    temporary.replace(index)
    validate_reference_index(index)
    print('REV2 subject references initialized.',flush=True)
    return root


def validate_reference_index(index):
    try:
        rows=json.loads(index.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise RuntimeError(f'Reference index cannot be read: {index}') from exc
    if not isinstance(rows,list):
        raise RuntimeError('Reference index must contain a list.')
    for row in rows:
        if not isinstance(row,dict) or not all(k in row for k in ('subject','grade','file')):
            raise RuntimeError('Reference index contains an invalid entry.')
        name=row['file']
        if not isinstance(name,str) or not name or name!=Path(name).name or not name.endswith('.docx'):
            raise RuntimeError('Reference index contains an unsafe filename.')
        if not (index.parent/name).is_file():
            raise RuntimeError(f'Reference document missing from persistent storage: {name}')


if __name__=='__main__': initialize_data()
