"""Verify real first-start setup and preservation across redeployment."""
import json
from pathlib import Path
from bootstrap import initialize_data


def test_empty_disk_gets_three_references_and_keeps_legacy_files(tmp_path):
    data=tmp_path/'data';refs=data/'references';refs.mkdir(parents=True)
    legacy=refs/'active.docx';legacy.write_bytes(b'legacy-reference')
    assert initialize_data(data)==data
    rows=json.loads((refs/'index.json').read_text())
    assert {(r['subject'],r['grade']) for r in rows}=={('math','10'),('chemistry','11'),('english','10')}
    assert all((refs/r['file']).read_bytes().startswith(b'PK') for r in rows)
    assert legacy.read_bytes()==b'legacy-reference'


def test_restart_preserves_reference_changes_and_deletions(tmp_path):
    initialize_data(tmp_path)
    refs=tmp_path/'references';index=refs/'index.json'
    rows=json.loads(index.read_text())
    deleted=rows.pop();(refs/deleted['file']).unlink()
    custom=refs/rows[0]['file'];custom.write_bytes(b'staff-replaced-reference')
    index.write_text(json.dumps(rows))
    before=index.read_bytes()
    initialize_data(tmp_path)
    assert index.read_bytes()==before
    assert custom.read_bytes()==b'staff-replaced-reference'
    assert not (refs/deleted['file']).exists()
