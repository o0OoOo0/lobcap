"""Append-only run evidence and canonical deterministic digests."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

from lobcap.backtest.types import jsonable


class MemorySink:
    """Small-fixture sink. Production runs use ArtifactSink."""
    def __init__(self):
        self.rows={}
        self._digest=hashlib.sha256()

    def write(self,channel,row):
        row=jsonable(row)
        self._digest.update((channel+":"+json.dumps(row,sort_keys=True,separators=(",", ":"))+"\n").encode())
        self.rows.setdefault(channel,[]).append(row)

    @property
    def digest(self):return self._digest.hexdigest()


class ArtifactSink:
    JSONL={"decisions","orders","fills","ledger","execution","timeline","observations"}
    CSV={"timeseries","markouts"}

    def __init__(self,path):
        self.path=Path(path)
        self.path.mkdir(parents=True,exist_ok=False)
        self._digest=hashlib.sha256()
        self.handles={}
        self.writers={}

    def write(self,channel,row):
        row=jsonable(row)
        encoded=json.dumps(row,sort_keys=True,separators=(",", ":"))
        self._digest.update((channel+":"+encoded+"\n").encode())
        if channel in self.JSONL|self.CSV:
            if channel not in self.handles:
                extension="jsonl" if channel in self.JSONL else "csv"
                gz=gzip.GzipFile(filename=str(self.path/f"{channel}.{extension}.gz"),mode="wb",mtime=0)
                self.handles[channel]=io.TextIOWrapper(gz,encoding="utf-8",newline="")
            stream=self.handles[channel]
            if channel in self.JSONL:stream.write(encoded+"\n")
            else:
                if channel not in self.writers:
                    writer=csv.DictWriter(stream,fieldnames=list(row))
                    writer.writeheader();self.writers[channel]=writer
                self.writers[channel].writerow(row)
        else:
            (self.path/f"{channel}.json").write_text(encoded+"\n",encoding="utf-8")

    @property
    def digest(self):return self._digest.hexdigest()

    def close(self):
        for stream in self.handles.values():stream.close()
