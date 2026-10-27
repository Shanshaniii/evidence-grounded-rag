"""Persistence helpers extracted from the production journal; no dispatch machinery."""
import pathlib,os
def durable_bytes(p,data):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.pending')
 with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 os.replace(tmp,p)
