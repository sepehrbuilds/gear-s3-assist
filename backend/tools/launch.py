"""Load the private candidate environment without shell evaluation, then run a module."""
import argparse
import os
from pathlib import Path
import re
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--env",required=True)
    p.add_argument("module",choices=["desk_stream.server","desk_stream.tts_gateway"])
    p.add_argument("args",nargs=argparse.REMAINDER)
    a=p.parse_args()
    path=Path(a.env)
    if path.stat().st_mode & 0o077:
        raise SystemExit("Private environment file must have mode 0600")
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):continue
        key,value=line.split("=",1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*",key):raise ValueError("invalid environment name")
        os.environ[key]=value
    sys.argv=[a.module]+a.args
    runpy.run_module(a.module,run_name="__main__")


if __name__=="__main__":main()
