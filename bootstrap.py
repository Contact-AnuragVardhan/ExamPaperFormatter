"""Seed bundled REV2 references on first start, after the disk is mounted."""
from pathlib import Path
import json
import os
import shutil
import records


def initialize_data(data_root=None):
    records.load_env()
    root=Path(data_root or os.getenv('EXAM_DATA_ROOT') or os.getenv('EXAM_DATA_DIR') or records.ROOT/'data')
    root.mkdir(parents=True,exist_ok=True)
    destination=root/'references'
    destination.mkdir(exist_ok=True)
    index=destination/'index.json'
    # Existing subject changes/deletions are authoritative; never reset them
    # to bundled defaults on a restart or redeploy.
    if index.exists(): return root
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
    print('REV2 subject references initialized.',flush=True)
    return root


if __name__=='__main__': initialize_data()
