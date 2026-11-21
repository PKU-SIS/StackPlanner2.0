"""Host lifecycle telemetry. Does not change graph progress, deadlines or retries."""
from contextlib import contextmanager
from pathlib import Path
import datetime,json,os,signal,threading,time


def write(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temp.replace(path)


def snapshot(state):
    return dict(state=state,pid=os.getpid(),time=time.time(),at=datetime.datetime.now(datetime.timezone.utc).isoformat())


@contextmanager
def lifecycle(out):
    stop=threading.Event();path=out/'supervisor-health.json'
    def beat():
        while not stop.is_set():
            write(path,snapshot('alive'))
            stop.wait(10)
    def terminate(signum,frame):
        raise SystemExit(128+signum)
    previous={s:signal.signal(s,terminate) for s in (signal.SIGTERM,signal.SIGINT)}
    worker=threading.Thread(target=beat,daemon=True);worker.start()
    terminal='exited'
    try:
        yield
    except BaseException as exc:
        terminal='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error'
        write(out/'supervisor-exit.json',dict(**snapshot(terminal),error_type=type(exc).__name__))
        raise
    finally:
        stop.set();worker.join(timeout=2)
        write(path,snapshot(terminal))
        for s,handler in previous.items():signal.signal(s,handler)
