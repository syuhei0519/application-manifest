# Linux user/mount/network namespaceでrender/parseを隔離する実行補助。trusted-render.pyが呼ぶ。
# MR由来入力へ資格情報・networkを与えず、時間/出力/資源を制限する。Linux専用前提はdocs/linux-render-boundary.md。
"""Linux Runner上のrender境界。専用user/mount/PID/network namespaceで実行する。

資格情報・kubeconfig・socket・repoルート・親の/procはmountしない。
固定Runner imageから/usrを用意し、信頼しない入力は読取専用でmountする。
"""
import hashlib
import io
import os
from pathlib import Path
import resource
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.request

HELM_ARCHIVE_SHA256 = 'c306b46f719b0a4da32d0f78ee21bf90ce8d602f15b22ab753f0674d1670a7f3'
HELM_URL = 'https://get.helm.sh/helm-v4.2.4-linux-amd64.tar.gz'

def prepare_helm(destination):
    # URLを固定し、環境からのproxy・callerが選ぶCA/redirectを使わない。
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')))
    data = bytearray(); deadline = time.monotonic()+60
    with opener.open(HELM_URL, timeout=8) as response:
        while chunk := response.read(256*1024):
            data.extend(chunk)
            if len(data)>96*1024*1024 or time.monotonic()>deadline:
                raise ValueError('fixed Helm download limit')
    if hashlib.sha256(data).hexdigest()!=HELM_ARCHIVE_SHA256:
        raise ValueError('fixed Helm archive checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        member=archive.getmember('linux-amd64/helm')
        if not member.isfile() or member.size>100*1024*1024:
            raise ValueError('invalid Helm archive entry')
        binary=archive.extractfile(member).read(100*1024*1024+1)
    destination.write_bytes(binary); destination.chmod(0o500)
    return hashlib.sha256(binary).hexdigest()

def cgroup_limits():
    memory=Path('/sys/fs/cgroup/memory.max').read_text().strip()
    cpu=Path('/sys/fs/cgroup/cpu.max').read_text().split()
    pids=Path('/sys/fs/cgroup/pids.max').read_text().strip()
    if memory=='max' or int(memory)>2*1024**3 or cpu[0]=='max' or int(cpu[0])/int(cpu[1])>2:
        raise ValueError('Runner CPU/memory bounds are absent or exceed the fixed profile')
    return {'memoryBytes':int(memory),'cpuCores':int(cpu[0])/int(cpu[1]),'processLimit':None if pids=='max' else int(pids),'wallSeconds':90,'stdoutStderrBytes':2*1024**2}

SETUP = r'''
set -eu
root=$1; shift
mount --make-rprivate /
mount --bind "$root" "$root"
mount -o remount,bind,ro "$root"
mount --bind /usr "$root/usr"
mount -o remount,bind,ro "$root/usr"
mount -t tmpfs -o "size=$1,nosuid,nodev,noexec" tmpfs "$root/tmp"; shift
mount -t proc -o ro,nosuid,nodev,noexec proc "$root/proc"
mount --bind /dev/null "$root/dev/null"
while [ "$1" != '--' ]; do
  source=$1; target=$2; access=$3; shift 3
  mount --bind "$source" "$root$target"
  if [ "$access" = ro ]; then mount -o remount,bind,ro "$root$target"; fi
done
shift
cd "$root"
exec /usr/sbin/chroot "$root" /usr/bin/setpriv --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs /usr/bin/env -i PATH=/usr/local/go/bin:/usr/bin:/bin HOME=/tmp GOCACHE=/tmp/cache GOPATH=/tmp/go GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=2 GOMEMLIMIT=768MiB HELM_PLUGINS=/nonexistent HELM_CACHE_HOME=/tmp/helm/cache HELM_CONFIG_HOME=/tmp/helm/config HELM_DATA_HOME=/tmp/helm/data KUBECONFIG=/nonexistent /bin/sh -ec "$1"
'''

def run(mounts, command, timeout=90, compiler=False):
    cgroup_limits()
    with tempfile.TemporaryDirectory(prefix='pe003a-ns-') as folder:
        base=Path(folder); root=base/'root'; root.mkdir()
        for name in ('usr','tmp','proc','dev','etc'):
            (root/name).mkdir()
        for name,target in (('bin','usr/bin'),('sbin','usr/sbin'),('lib','usr/lib'),('lib64','usr/lib64')):
            (root/name).symlink_to(target, target_is_directory=True)
        (root/'dev/null').touch()
        args=['/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--fork','--net','/bin/sh','-ec',SETUP,'namespace-setup',str(root),'512m' if compiler else '64m']
        for source,target,readonly in mounts:
            if target not in ('/src','/out','/input','/trusted'):
                raise ValueError('unexpected namespace mount')
            (root/target[1:]).mkdir()
            args += [str(source),target,'ro' if readonly else 'rw']
        args += ['--',command]
        def bounds():
            # pids.maxでこのcontainerを制限する。RLIMIT_NPROCは同じmapped UIDを使う無関係なhost processまで数えるのでここには不適切。
            resource.setrlimit(resource.RLIMIT_CPU,(60,60))
            resource.setrlimit(resource.RLIMIT_DATA,((1024 if compiler else 512)*1024**2,)*2)
            resource.setrlimit(resource.RLIMIT_FSIZE,((64 if compiler else 20)*1024**2,)*2)
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        out=base/'stdout'; err=base/'stderr'
        with out.open('wb') as stdout, err.open('wb') as stderr:
            proc=subprocess.Popen(args,env={'PATH':'/usr/bin:/bin:/usr/local/go/bin'},stdin=subprocess.DEVNULL,
                stdout=stdout,stderr=stderr,preexec_fn=bounds,start_new_session=True)
            deadline=time.monotonic()+timeout
            try:
                while proc.poll() is None:
                    if time.monotonic()>deadline or out.stat().st_size+err.stat().st_size>2*1024**2:
                        raise ValueError('namespace time/output limit')
                    time.sleep(0.05)
                if proc.returncode or out.stat().st_size+err.stat().st_size>2*1024**2:
                    raise ValueError('namespace failed; diagnostics suppressed')
                return out.read_bytes()
            finally:
                if proc.poll() is None:
                    import signal
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=10)
