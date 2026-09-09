import gzip
import json
from dataclasses import replace
from decimal import Decimal as D

import pytest
import yaml

from lobcap.backtest.replay import write_json,load_sample,sample_fingerprint,iter_events
from lobcap.backtest.runner import run_one,run_sweep,config_document,main,_RUN_LOCK
from lobcap.backtest.types import BookSnapshot,ReplayEvent,L2Trade,jsonable
from lobcap.config.backtest import load_backtest_config,variant
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT


@pytest.fixture
def sample(tmp_path):
    config=replace(load_backtest_config(ROOT/"configs/backtest/binance_demo.yaml"),output_path=tmp_path/"outputs")
    path=tmp_path/"sample";path.mkdir()
    initial=BookSnapshot(((D(100),D(1)),),((D(101),D(1)),),config.sample.start_ns-30_000_000_000,0,D(90),D(110))
    event=ReplayEvent(1,config.sample.start_ns+100_000_000,config.sample.start_ns+100_000_000,"trade",
        ("synthetic:1",),trade=L2Trade("buy",D(100),D(".001")))
    write_json(path/"initial_book.json",initial)
    write_json(path/"validation.json",{"status":"PASS","synthetic":True})
    with gzip.open(path/"events.jsonl.gz","wt") as f:f.write(json.dumps(jsonable(event))+"\n")
    files={name:sha256_file(path/name) for name in ("initial_book.json","events.jsonl.gz","validation.json")}
    write_json(path/"sample_manifest.json",{"status":"PASS","sample":config.sample,"instrument":config.instrument,
        "files":files,"sample_sha256":sample_fingerprint(files,config.sample)})
    return config,path


def test_export_cli_parity_and_sweep_order(sample,tmp_path,capsys):
    config,path=sample
    config=variant(config,alpha=None,stale_book_ms=100000)
    direct=run_one(config,path)
    exported=direct.output_dir/"config.yaml"
    assert load_backtest_config(exported).effective_sha256==config.effective_sha256
    main(["run","--config",str(exported),"--sample",str(path)])
    cli=json.loads(capsys.readouterr().out)
    assert cli["digest"]==direct.digest
    _,a=run_sweep(config,[{"alpha":0},{"alpha":1}],path)
    _,b=run_sweep(config,[{"alpha":1},{"alpha":0}],path)
    assert {r["fill_model"]:r["deterministic_digest"] for r in a}=={r["fill_model"]:r["deterministic_digest"] for r in b}


def test_sweep_cap_lock_and_sample_integrity(sample):
    config,path=sample
    with pytest.raises(ValueError):run_sweep(config,[{}]*25,path)
    _RUN_LOCK.acquire()
    try:
        with pytest.raises(RuntimeError):run_one(config,path)
    finally:_RUN_LOCK.release()
    with (path/"events.jsonl.gz").open("ab") as f:f.write(b"tampered")
    with pytest.raises(ValueError,match="checksum"):load_sample(path,config)


def test_reader_rejects_hidden_nested_trade_fields(sample):
    _,path=sample
    with gzip.open(path/"events.jsonl.gz","rt") as f:row=json.loads(f.readline())
    row["trade"]["maker_order_id"]="hidden"
    with gzip.open(path/"events.jsonl.gz","wt") as f:f.write(json.dumps(row)+"\n")
    with pytest.raises(ValueError,match="trade fields"):list(iter_events(path))
