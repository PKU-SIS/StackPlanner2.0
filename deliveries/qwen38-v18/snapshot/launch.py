"""Run the generation supervisor under active monitoring and retain exit records."""
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def main():
    with (ROOT/'local-storage/supervisor.log').open('a') as stream:
        result = subprocess.run([sys.executable,'-B',str(ROOT/'speed_trial.py')],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    record=dict(returncode=result.returncode,ended_at=datetime.now(timezone.utc).isoformat(),automatic_restart=False)
    (ROOT/'local-storage/launcher-exit.json').write_text(json.dumps(record,indent=2)+'\n')
    return result.returncode


if __name__=='__main__':
    sys.exit(main())
