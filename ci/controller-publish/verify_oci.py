#!/usr/bin/env python3
"""Verify the exact reviewed OCI artifact before any registry operation."""
import gzip
import hashlib
import io
import json
import pathlib
import re
import sys
import tarfile

ARCHIVE = 'bd5a2f85d4a6301b84247ebc61d2eabdeaa0929165303943b6dc814924ec85e8'
MANIFEST = 'sha256:3db562b99a4b200285eaf756a03dc8d90c5d00c7579e2802ab6fccb50d65cdec'
CONFIG = 'sha256:08dd8a1ea8d94fde98319f3d3e40bb3ed4a3565b2724b8e08a150e09662e448e'
BINARY = '02cdb9d92d5570c1fbb9cdebf4b354222c05be053ebbf7b84b80b443d68beacb'

def digest(b): return hashlib.sha256(b).hexdigest()

def verify(archive, layout):
    archive, layout = pathlib.Path(archive), pathlib.Path(layout)
    if digest(archive.read_bytes()) != ARCHIVE: raise ValueError('archive-sha256')
    layout.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as tar:
        seen = set()
        for member in tar:
            name = member.name.rstrip('/')
            if name in seen: raise ValueError('duplicate-entry')
            seen.add(name)
            if member.isdir():
                if name not in ('blobs', 'blobs/sha256'): raise ValueError('archive-directory')
                (layout/name).mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                if name not in ('index.json','oci-layout') and not re.fullmatch(r'blobs/sha256/[a-f0-9]{64}',name):
                    raise ValueError('archive-entry')
                (layout/name).parent.mkdir(parents=True, exist_ok=True)
                (layout/name).write_bytes(tar.extractfile(member).read())
            else: raise ValueError('archive-type')
    def blob(desc):
        algorithm, hexdigest = desc['digest'].split(':',1)
        if algorithm != 'sha256' or not re.fullmatch(r'[a-f0-9]{64}',hexdigest): raise ValueError('blob-digest')
        value = (layout/'blobs'/'sha256'/hexdigest).read_bytes()
        if len(value) != desc['size'] or digest(value) != hexdigest: raise ValueError('blob-integrity')
        return value
    index=json.loads((layout/'index.json').read_bytes())
    if len(index['manifests']) != 1 or index['manifests'][0]['digest'] != MANIFEST: raise ValueError('manifest')
    manifest=json.loads(blob(index['manifests'][0]))
    if manifest['config']['digest'] != CONFIG: raise ValueError('config')
    config=json.loads(blob(manifest['config']))
    layers=manifest['layers']
    if config.get('os') != 'linux' or config.get('architecture') != 'amd64' or len(layers)!=16: raise ValueError('platform')
    diffids=config['rootfs']['diff_ids']
    if len(diffids)!=len(layers): raise ValueError('diffids')
    plugin = []
    for i,desc in enumerate(layers):
        compressed=blob(desc)
        plain=gzip.decompress(compressed) if desc['mediaType'].endswith('+gzip') else compressed
        if 'sha256:'+digest(plain) != diffids[i]: raise ValueError('layer-diffid')
        with tarfile.open(fileobj=io.BytesIO(plain),mode='r:') as tar:
            for item in tar:
                if item.name.lstrip('./')=='opt/account-policy':
                    if not item.isfile() or item.mode != 0o555 or digest(tar.extractfile(item).read()) != BINARY: raise ValueError('plugin')
                    plugin.append(i)
    if plugin != [15]: raise ValueError('plugin-layer')
    return {'archive':ARCHIVE,'manifest':MANIFEST,'config':CONFIG,'binary':BINARY,'layers':len(layers)}

if __name__ == '__main__':
    try: print(json.dumps(verify(*sys.argv[1:])))
    except (OSError,ValueError,KeyError,IndexError,TypeError,tarfile.TarError): raise SystemExit('OCI verification failed')
