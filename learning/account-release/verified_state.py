# 検証済みの状態と履歴を、同時更新による取り違えや途中書き込みを防いで保存します。
"""Atomic, exclusive updates to the server-owned observation history.

A stale lock stops updates and requires operator inspection; it is never broken
automatically. Readers only see complete JSON files. No browser route writes it.
"""
import json
import os
from pathlib import Path
import tempfile


def update(path, mutate):
    path=Path(path);lock=path.with_suffix(path.suffix+'.lock')
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    temporary=None
    try:
        os.close(fd)
        state=json.loads(path.read_text()) if path.exists() else {}
        mutate(state)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as output:
            temporary=Path(output.name)
            json.dump(state,output,sort_keys=True,allow_nan=False)
            output.flush();os.fsync(output.fileno())
        os.replace(temporary,path);temporary=None
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
        lock.unlink()
