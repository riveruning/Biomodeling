"""Minimal Bubblewrap filesystem for a single evaluation workspace."""
from pathlib import Path
import os

PROJECT=Path(__file__).resolve().parents[2]
CANDIDATE=PROJECT/'artifacts/holdout-evaluation-20261017/candidate'
CODEX=Path('/home/riveruning/.local/bin/codex').resolve()


def base(work, *, network=True):
    cmd=['bwrap','--die-with-parent','--unshare-user','--unshare-pid','--unshare-ipc','--unshare-uts',
         '--new-session','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
         '--proc','/proc','--dev','/dev','--tmpfs','/tmp',
         '--ro-bind','/etc/ssl','/etc/ssl','--ro-bind','/etc/resolv.conf','/etc/resolv.conf',
         '--ro-bind','/etc/hosts','/etc/hosts','--ro-bind','/etc/nsswitch.conf','/etc/nsswitch.conf',
         '--bind',str(work),'/work','--chdir','/work']
    if not network:cmd.append('--unshare-net')
    return cmd


def product_command(work, phase, runner):
    cmd=base(work)
    for path in [PROJECT/'.venv',CANDIDATE/'src']:
        cmd+=['--ro-bind',str(path),str(path)]
    cmd+=['--ro-bind',str(runner),'/runner.py','--setenv','PYTHONPATH',str(CANDIDATE/'src'),
          '--',str(PROJECT/'.venv/bin/python'),'/runner.py','--work','/work','--phase',phase]
    return cmd


def codex_command(work, prompt):
    cmd=base(work)
    auth=Path.home()/'.codex/auth.json'
    cmd+=['--ro-bind',str(CODEX.parent),str(CODEX.parent),
          '--ro-bind',str(auth),str(auth),'--',str(CODEX),'exec',
          '--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check',
          '--sandbox','workspace-write','--model','gpt-6.1-sol',
          '-c','model_reasoning_effort="medium"','--json','-C','/work',prompt]
    return cmd


def clean_environment(key=None):
    # Authentication is passed only to the appropriate inference runtime.
    env={k:v for k,v in os.environ.items() if k in
         {'PATH','HOME','USER','LANG','LC_ALL','TERM','SSL_CERT_FILE','SSL_CERT_DIR'}}
    if key is not None:env['BIOMODEL_API_KEY']=key
    env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    return env
