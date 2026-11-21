"""Read public progress and ledger state; never resume generation."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT/'local-storage/runs/band10000-case10-outline-selected'


def read(path, fallback=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def snapshot():
    health = read(ROOT/'local-storage/supervisor-health.json', {})
    now = datetime.now(timezone.utc)
    # Exec cells can use separate PID namespaces. Use the durable heartbeat,
    # not a PID probe from a different cell, as the public liveness signal.
    heartbeat_age = now.timestamp()-health['time'] if health.get('time') else None
    alive = bool(health.get('state') == 'alive' and heartbeat_age is not None and heartbeat_age < 35)
    attempt = read(RUN/'attempt.json', {})
    state = read(RUN/'state.json', {})
    ledgers = list((RUN/'runtime').rglob('report/r1/ledger.json')) if (RUN/'runtime').exists() else []
    ledger = read(ledgers[0], {}) if len(ledgers) == 1 else {}
    completed, last_event, last_step = {}, None, None
    events = RUN/'events.jsonl'
    if events.exists():
        with events.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                data = event.get('data')
                last_event = event.get('time')
                if event.get('mode') != 'custom' or not isinstance(data,dict):
                    continue
                if data.get('type') in {'task_started','task_completed','task_message','task_step'}:
                    last_step = {k:data[k] for k in ('type','task_id','agent_type','subagent_type','step') if k in data}
                if data.get('type') == 'task_completed' and data.get('usage'):
                    identity = (data.get('task_id') or data.get('a2a_task_id'), (data.get('result_envelope') or {}).get('task_id'))
                    completed[identity] = data['usage']
    tokens = sum((m.get('usage_metadata') or {}).get('total_tokens',0) for m in state.get('messages',[]) if isinstance(m,dict))
    tokens += sum(v.get('total_tokens',0) for v in completed.values())
    row = dict(at=now.isoformat(), supervisor_heartbeat_fresh=alive, heartbeat_age_seconds=heartbeat_age, heartbeat=health,
        elapsed_wall_seconds=round((now-datetime.fromisoformat(attempt['started_at'])).total_seconds(),1) if attempt else None,
        last_event_at=datetime.fromtimestamp(last_event,timezone.utc).isoformat() if last_event else None,
        last_task_event=last_step, stage=state.get('sp_current_stage'),
        required_supplement_registration=ledger.get('supplementary_registration_required'),
        tree_version=(ledger.get('tree') or {}).get('version'),
        research_actions=[{k:a.get(k) for k in ('action_id','kind','status','gap_ids')} for a in (ledger.get('actions') or {}).values()],
        gaps=[{k:g.get(k) for k in ('gap_id','status','rounds','node_ids')} for g in ledger.get('gaps',[])],
        current_drafts=sum(bool(d.get('is_current')) for d in ledger.get('drafts',[])),
        review_status=(ledger.get('review') or {}).get('status'),
        delivered=bool(ledger.get('delivered')), accounted_tokens_lower_bound=tokens,
        completed_attempt_count=len(completed), metrics=read(RUN/'metrics.json'),
        process_result=read(RUN/'process-result.json'), launcher_exit=read(ROOT/'local-storage/launcher-exit.json'),
        race_started=False)
    return row


if __name__=='__main__':
    print(json.dumps(snapshot(),ensure_ascii=False,indent=2))
