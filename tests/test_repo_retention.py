import json
from pathlib import Path
import pytest
from mercury.repo.retention import retention_plan, prune_generations
from mercury.repo.offline_clone import OFFLINE_SYNC_RECEIPT_NAME as RECEIPT


def seed(root, day, suffix='120000-12345678', success=True):
    name=f'202610{day:02d}T{suffix}'
    p=root/'generations'/name
    p.mkdir(parents=True)
    data={'repositories':[{'synced':success,'fsck':'PASS' if success else 'FAIL'}]}
    (p/RECEIPT).write_text(json.dumps(data))
    (root/RECEIPT).write_text(json.dumps(data))
    return p


def test_retention_preview_keeps_daily_monthly_and_incomplete(tmp_path):
    paths=[seed(tmp_path,day) for day in range(1,11)]
    incomplete=seed(tmp_path,2,'130000-aaaaaaaa',success=False)
    before=sorted(str(p) for p in tmp_path.rglob('*'))
    plan=prune_generations(tmp_path)
    assert plan['candidates']==[paths[1].name, paths[2].name]
    assert paths[0].name in plan['keep'] and incomplete.name in plan['keep']
    assert sorted(str(p) for p in tmp_path.rglob('*'))==before
    with pytest.raises(ValueError, match='disabled'):
        prune_generations(tmp_path,execute=True)


def test_retention_pins_latest_receipt_and_snapshot_dependency(tmp_path):
    paths=[seed(tmp_path,day) for day in range(1,11)]
    data={'repositories':[{'synced':True,'fsck':'PASS','snapshot':{'path':str(paths[1]/'snapshot')}}]}
    (tmp_path/RECEIPT).write_text(json.dumps(data))
    assert paths[1].name in retention_plan(tmp_path)['keep']


def test_explicit_pruning_removes_only_candidates(tmp_path,monkeypatch):
    paths=[seed(tmp_path,day) for day in range(1,11)]
    outside=tmp_path/'outside';outside.mkdir()
    (tmp_path/'generations'/'20261002T120000-eeeeeeee').symlink_to(outside,target_is_directory=True)
    monkeypatch.setattr('mercury.core.usb_mount.assert_operator_storage_path',lambda *a,**k:None)
    result=prune_generations(tmp_path,execute=True,enabled=True)
    assert len(result['candidates'])==2
    assert outside.is_dir()
    assert paths[0].exists() and paths[-1].exists()
    assert not paths[1].exists() and not paths[2].exists()
