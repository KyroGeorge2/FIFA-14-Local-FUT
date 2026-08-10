#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3
from pathlib import Path

LEGEND_IDS=[190043,1109,192181,191189,10264,177839,4000,5589,214100,214267,1183,6235,166124,161840,51539,1116,164000,5419,1605,13128,1025,5680,214098,214649,1,805,1419,1198,215732,214101,7512,117106,1201,942,52241,388,393,6975,244,177845,195,215558]

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest().upper()

def inspect_sqlite(path: Path):
    out={'sqlite':False,'tables':[],'legendIdHits':{}}
    try:
        if path.read_bytes()[:16] != b'SQLite format 3\x00':
            return out
        out['sqlite']=True
        con=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)
        con.row_factory=sqlite3.Row
        for row in con.execute("select name from sqlite_master where type='table' order by name"):
            t=row['name']
            cols=[r['name'] for r in con.execute(f'pragma table_info("{t}")')]
            out['tables'].append({'name':t,'columns':cols})
            idcols=[c for c in cols if c.lower() in {'playerid','player_id','assetid','asset_id','id'}]
            if not idcols: continue
            for c in idcols:
                try:
                    marks=','.join('?' for _ in LEGEND_IDS)
                    rows=con.execute(f'SELECT "{c}" FROM "{t}" WHERE "{c}" IN ({marks})',LEGEND_IDS).fetchall()
                    vals=sorted({int(r[0]) for r in rows if r[0] is not None})
                    if vals:
                        out['legendIdHits'][f'{t}.{c}']=vals
                except sqlite3.Error:
                    pass
        con.close()
    except Exception as exc:
        out['error']=str(exc)
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--game-root',required=True)
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    root=Path(args.game_root).resolve()
    candidates=[]
    explicit=[
        root/'data'/'db'/'fifa_ng_db.db', root/'data'/'db'/'fifa_ng_db-meta.xml',
        root/'data'/'fifa_ng_db.db', root/'fifa_ng_db.db',
    ]
    for p in explicit:
        if p.exists(): candidates.append(p)
    # Also include nearby DB metadata and the BIG/BH containers that would need
    # regeneration if the DB is still archive-only.
    for base in (root, root/'data', root/'data'/'db'):
        if not base.exists(): continue
        try:
            for p in base.iterdir():
                n=p.name.lower()
                if p.is_file() and (n.endswith(('.db','.sqlite','.sqlite3','.xml')) and ('fifa' in n or 'db' in n) or n.endswith(('.big','.bh'))):
                    candidates.append(p)
        except OSError:
            pass
    uniq=[]; seen=set()
    for p in candidates:
        k=str(p).lower()
        if k in seen: continue
        seen.add(k); uniq.append(p)
    files=[]
    for p in uniq:
        try:
            row={'path':str(p),'name':p.name,'size':p.stat().st_size}
            if p.stat().st_size <= 256*1024*1024:
                row['sha256']=sha256(p)
            if p.suffix.lower() in {'.db','.sqlite','.sqlite3'}:
                row.update(inspect_sqlite(p))
            files.append(row)
        except Exception as exc:
            files.append({'path':str(p),'error':str(exc)})
    doc={
        'schema':'fifa14-v24022-client-db-scan',
        'gameRoot':str(root),
        'files':files,
        'legendAssetIds':LEGEND_IDS,
        'note':'Read-only scan. No FIFA game database or archive is modified.',
    }
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(doc,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'files':len(files),'sqliteFiles':sum(1 for x in files if x.get('sqlite'))}))
if __name__=='__main__': main()
