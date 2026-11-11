"""Disk validation and storage directory compatibility."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bootstrap import initialize_data
from storage_health import validate_data_root


def test_render_production_requires_real_mount(tmp_path,monkeypatch):
    monkeypatch.setenv('RENDER','true')
    monkeypatch.setenv('APP_ENV','prod')
    monkeypatch.delenv('REQUIRE_PERSISTENT_DISK',raising=False)
    root=tmp_path/'not-mounted'
    with pytest.raises(RuntimeError,match='Persistent disk is not mounted'):
        validate_data_root(root)
    assert not root.exists()


def test_render_production_does_not_seed_unmounted_folder(tmp_path,monkeypatch):
    monkeypatch.setenv('RENDER','true')
    monkeypatch.setenv('APP_ENV','prod')
    monkeypatch.delenv('REQUIRE_PERSISTENT_DISK',raising=False)
    with pytest.raises(RuntimeError,match='Persistent disk is not mounted'):
        initialize_data(tmp_path/'not-mounted')


def test_development_directory_is_writable(tmp_path,monkeypatch):
    monkeypatch.delenv('RENDER',raising=False)
    monkeypatch.delenv('REQUIRE_PERSISTENT_DISK',raising=False)
    root=tmp_path/'data'
    assert validate_data_root(root)==root
    assert root.is_dir()
    assert not list(root.iterdir())


def test_mount_validation_accepts_real_mount(tmp_path,monkeypatch):
    monkeypatch.setenv('RENDER','true')
    monkeypatch.setenv('APP_ENV','prod')
    monkeypatch.delenv('REQUIRE_PERSISTENT_DISK',raising=False)
    monkeypatch.setattr('storage_health._is_mounted_directory',lambda _: True)
    assert validate_data_root(tmp_path/'data').is_dir()


def test_records_standalone_honors_environment_root(tmp_path):
    variables=os.environ.copy()
    variables['EXAM_DATA_ROOT']=str(tmp_path/'persistent')
    variables.pop('EXAM_DATA_DIR',None)
    result=subprocess.run([sys.executable,'-c','import records; print(records.DATA)'],
                          capture_output=True,text=True,env=variables,check=True)
    assert result.stdout.strip()==str(tmp_path/'persistent')


def test_existing_reference_index_missing_document_fails(tmp_path):
    root=initialize_data(tmp_path)
    index=root/'references'/'index.json'
    import json
    first=json.loads(index.read_text(encoding='utf-8'))[0]
    (root/'references'/first['file']).unlink()
    with pytest.raises(RuntimeError,match='Reference document missing'):
        initialize_data(tmp_path)
