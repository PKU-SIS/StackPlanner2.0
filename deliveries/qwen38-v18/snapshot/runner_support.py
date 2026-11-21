"""Nine held-out native SP2 runs using the three accepted, immutable profiles."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tarfile
from urllib.parse import urlsplit

OUT = Path('/data2/pkuiti/zmy/codex-relay-work/length-generalization-20261116')
REPO = Path('/data2/pkuiti/zmy/projects/reviews/StackPlanner2-sp2DeepResearch-20261103')
ART = REPO / 'artifacts'
CASES = (6, 30, 35)
BANDS = (10000, 20000, 30000)
TIMEOUT = 2100


def configure_storage():
    """Keep this supervisor and all descendants' writable storage on /data2."""
    import tempfile

    storage = OUT / 'local-storage'
    paths = {
        'TMPDIR': storage / 'tmp',
        'TMP': storage / 'tmp',
        'TEMP': storage / 'tmp',
        'XDG_CACHE_HOME': storage / 'cache',
        'UV_CACHE_DIR': storage / 'cache/uv',
        'PIP_CACHE_DIR': storage / 'cache/pip',
        'HF_HOME': storage / 'cache/huggingface',
        'HF_HUB_CACHE': storage / 'cache/huggingface/hub',
        'TORCH_HOME': storage / 'cache/torch',
        'MPLCONFIGDIR': storage / 'cache/matplotlib',
        'PYTHONPYCACHEPREFIX': storage / 'cache/pycache',
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    tempfile.tempdir = str(paths['TMPDIR'])
    os.chdir(OUT)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def restore_archive(folder, prefix, destination):
    archive = folder / 'accepted-source-and-results.tar.gz'
    assert sha(archive) == read(folder / 'VERIFICATION.json')['archive_sha256']
    all_hashes = read(folder / 'CONTENTS_SHA256.json')
    expected = {name[len(prefix):]: digest for name, digest in all_hashes.items()
                if name.startswith(prefix)}
    assert expected
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            if not member.name.startswith(prefix):
                continue
            rel = member.name[len(prefix):]
            target = (destination / rel).resolve()
            assert member.isfile() and target.is_relative_to(destination.resolve())
            assert rel in expected
            data = stream.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == expected[rel]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    verify(destination, expected)
    return expected


def verify(folder, expected):
    actual = {str(p.relative_to(folder)): sha(p)
              for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual != expected:
        raise RuntimeError('frozen_source_hash_mismatch: ' + str(folder))


def prepare():
    manifest_path = OUT / 'manifest.json'
    if manifest_path.exists():
        manifest = read(manifest_path)
        for band in BANDS:
            verify(OUT / f'profiles/{band}/code', manifest['profiles'][str(band)]['hashes'])
        return manifest
    profiles = {}
    folder = ART / 'accepted-target10000-20261115'
    prefix = 'cells/case11-auto-n-body4000-cards1/native/code/'
    hashes = restore_archive(folder, prefix, OUT / 'profiles/10000/code')
    # The process-local 10k overlay is part of the accepted profile, not a new policy.
    driver_dir = OUT / 'profiles/10000/driver'
    driver_dir.mkdir()
    with tarfile.open(folder / 'accepted-source-and-results.tar.gz') as stream:
        data = stream.extractfile('experiment.py').read()
    assert hashlib.sha256(data).hexdigest() == read(folder / 'CONTENTS_SHA256.json')['experiment.py']
    (driver_dir / 'experiment.py').write_bytes(data)
    profiles['10000'] = dict(hashes=hashes, source=str(folder), driver_sha256=sha(driver_dir / 'experiment.py'),
                            contract=dict(
                                min_words=1, target_words=100000, max_words=100000,
                                compact_outline=True, writer_task_cards=True, length_auto_revision=False),
                            body_target_words=4000)
    historical = ART / 'rollback-approved-steps12-20261114'
    restore = load('restore_20k', historical / 'restore_to.py')
    restore.restore(11, OUT / 'profiles/20000/code')
    hashes = read(historical / 'manifest.json')['cases']['11']['code_hashes']
    profiles['20000'] = dict(hashes=hashes, source=str(historical),
                            contract=read(historical / 'cases/11/query.json')['report_length'])
    folder = ART / 'accepted-target30000-arguments4-20261116'
    hashes = restore_archive(folder, 'cases/11/code/', OUT / 'profiles/30000/code')
    profiles['30000'] = dict(hashes=hashes, source=str(folder),
                            contract=read(folder / 'ACCEPTED_CONFIG.json')['whole_document_contract'])
    queries = {q['id']: q for q in map(json.loads, (REPO / 'query.jsonl').read_text().splitlines())}
    prepared = read(ART / 'report-length-generalization-20261116/PREPARATION.json')
    cells = []
    for band in BANDS:
        for case in CASES:
            query_path = ART / f'report-length-generalization-20261116/queries/{case}.json'
            expected = next(q['query_sha256'] for q in prepared['selected_tasks'] if q['case_id'] == case)
            assert sha(query_path) == expected
            query = read(query_path)
            assert query == queries[case]
            name = f'band{band}-case{case}'
            directory = OUT / 'local-storage' / 'runs' / name
            directory.mkdir(parents=True, exist_ok=False)
            save(directory / 'query.json', {**query, 'report_length': profiles[str(band)]['contract']})
            (directory / 'request.md').write_text(query['prompt'])
            cells.append(dict(name=name, case_id=case, target_words=band, query_sha256=sha(directory / 'query.json')))
    manifest = dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(), cases=list(CASES), bands=list(BANDS),
                    profiles=profiles, cells=cells, replicates=1,
                    measurement='mixed-word-estimate-v1 after final citation rendering; not desktop Word',
                    acceptance='Report signed deviation plus +/-5% and +/-10%; no retrospective threshold change',
                    automatic_restart=False, race_requested=False, historical_gates_unchanged=True)
    save(manifest_path, manifest)
    return manifest


def cell_spec(name):
    manifest = prepare()
    return manifest, next(c for c in manifest['cells'] if c['name'] == name)


def native_for(band):
    code = OUT / f'profiles/{band}/code'
    os.environ['DEER_FLOW_SKILLS_PATH'] = str(code / 'skills')
    sys.path[:0] = [str(code / 'backend/scripts'), str(code / 'backend'), str(code / 'backend/packages/harness')]
    import evaluate_report_full as native
    native.ROOT = REPO
    return native


def preflight():
    import yaml
    from dotenv import load_dotenv
    load_dotenv(REPO / '.env', override=True)
    config = yaml.safe_load((REPO / 'config.yaml').read_text())
    model = config['models'][0]
    def resolve(value):
        return os.getenv(value[1:], '') if isinstance(value, str) and value.startswith('$') else value
    base = resolve(model.get('api_base') or model.get('base_url') or 'https://api.deepseek.com')
    host = urlsplit(base).hostname
    checks = []
    for endpoint in sorted({host, 'api.deepseek.com'} - {None}):
        try:
            with socket.create_connection((endpoint, 443), timeout=10):
                pass
            checks.append(dict(host=endpoint, reachable=True))
        except OSError as error:
            checks.append(dict(host=endpoint, reachable=False, error_type=type(error).__name__, errno=error.errno))
    ready = bool(resolve(model.get('api_key', ''))) and bool(os.getenv('LLM_API_KEY'))
    result = dict(checks=checks, credentials_configured=ready,
                  ready=ready and bool(checks) and all(c['reachable'] for c in checks),
                  paid_model_calls_started=False)
    save(OUT / 'local-storage' / 'preflight.json', result)
    return result


def worker(name):
    manifest, cell = cell_spec(name)
    band = cell['target_words']
    directory = OUT / 'local-storage' / 'runs' / name
    os.chdir(directory)
    if (directory / 'checkpoints.sqlite').exists():
        raise RuntimeError('existing_attempt_refuse_paid_restart')
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(REPO), DEER_FLOW_HOME=str(directory / 'runtime'))
    native = native_for(band)
    if band == 10000:
        driver_path = OUT / 'profiles/10000/driver/experiment.py'
        assert sha(driver_path) == manifest['profiles']['10000']['driver_sha256']
        driver = load('accepted_10k_driver', driver_path)
        driver.ROOT = OUT / 'profiles/10000/code'
        driver.install_overlay(dict(name=name, case_id=cell['case_id'], task_cards=True,
                                    body_target_words=4000, body_max_words=4000,
                                    argument_target=4, argument_max=6), directory)
    from dotenv import load_dotenv
    load_dotenv(REPO / '.env', override=True)
    native.worker(directory, 0, TIMEOUT)


def measure(name):
    _, cell = cell_spec(name)
    directory = OUT / 'local-storage' / 'runs' / name
    native_for(cell['target_words'])
    from deerflow.sp.report.citation_display import render_final_citations
    from deerflow.sp.report.length import estimate_words
    ledgers = list((directory / 'runtime').rglob('report/r1/ledger.json'))
    if len(ledgers) != 1:
        return dict(**cell, status='no_unique_ledger', words=None)
    ledger = read(ledgers[0])
    candidate = ledger.get('candidate') or {}
    if not candidate.get('path') or not Path(candidate['path']).exists():
        return dict(**cell, status='no_candidate', words=None)
    text = Path(candidate['path']).read_text()
    if hashlib.sha256(text.encode()).hexdigest() != candidate['sha256']:
        raise RuntimeError('candidate_sha_mismatch')
    rendered, _ = render_final_citations(text, candidate.get('source_table') or [])
    report_path = directory / 'evaluated-report.md'
    report_path.write_text(rendered)
    words = estimate_words(rendered)
    findings = (ledger.get('review') or {}).get('findings') or []
    blocking = [f for f in findings if f.get('severity') == 'blocking']
    # Keep all findings: the native length gate and content blockers are distinct.
    state = read(directory / 'state.json') if (directory / 'state.json').exists() else {}
    central = sum((m.get('usage_metadata') or {}).get('total_tokens', 0)
                  for m in state.get('messages', []) if isinstance(m, dict))
    attempts = {}
    if (directory / 'events.jsonl').exists():
        for line in (directory / 'events.jsonl').read_text().splitlines():
            event = json.loads(line)
            data = event.get('data')
            if event.get('mode') == 'custom' and isinstance(data, dict) and data.get('type') == 'task_completed' and data.get('usage'):
                attempt_id = (data.get('result_envelope') or {}).get('task_id')
                if not attempt_id: raise RuntimeError('missing_native_attempt_identity')
                key = (data.get('task_id') or data.get('a2a_task_id'), attempt_id)
                if key in attempts and attempts[key] != data['usage']: raise RuntimeError('inconsistent_duplicate_usage')
                attempts[key] = data['usage']
    tokens = central + sum(u.get('total_tokens', 0) for u in attempts.values())
    deviation = words - cell['target_words']
    row = dict(**cell, status='candidate', words=words, deviation=deviation,
               deviation_percent=round(deviation / cell['target_words'] * 100, 2),
               within_5_percent=abs(deviation) <= cell['target_words'] * .05,
               within_10_percent=abs(deviation) <= cell['target_words'] * .1,
               formal_delivered=bool(ledger.get('delivered')), review=ledger.get('review'),
               blocking_findings=blocking, report_sha256=sha(report_path),
               generation_tokens=tokens, tokens_include_cached_input=True)
    save(directory / 'observation.json', row)
    return row


def summary():
    manifest = prepare()
    rows = []
    for cell in manifest['cells']:
        path = OUT / 'local-storage' / 'runs' / cell['name'] / 'observation.json'
        rows.append(read(path) if path.exists() else dict(**cell, status='not_completed', words=None))
    save(OUT / 'local-storage' / 'results.json', rows)
    lines = ['# 三档新题篇幅验证', '', '每题每档一次；沿用冻结配置和历史交付门槛。字数为引用显示后的本地混合估算，非Word实测。', '',
             '| Case | 目标 | 实际字数 | 偏差 | ±10% | 正式交付 | 状态 |', '|---|---:|---:|---:|---|---|---|']
    for row in rows:
        lines.append(f"| {row['case_id']} | {row['target_words']} | {row.get('words')} | {row.get('deviation_percent', '—')}% | {row.get('within_10_percent', '—')} | {row.get('formal_delivered', '—')} | {row['status']} |")
    lines.extend(['', '未完成项不能记为字数达标；完整候选、内容审查和正式交付分别记录。', '每配置每题只有一次，不能证明跨次稳定性。'])
    (OUT / 'local-storage' / 'RESULTS.md').write_text('\n'.join(lines) + '\n')
    return rows


def validate_race_inputs(cell):
    """Bind the new case to the same pinned official query, rubric and reference."""
    prior = ART / 'drbench-paired-20261114'
    hashes = read(prior / 'source-hashes.json')
    for rel, expected in hashes.items():
        if sha(prior / 'upstream' / rel) != expected:
            raise RuntimeError('pinned_race_source_hash_mismatch: ' + rel)
    query = read(OUT / 'local-storage' / 'runs' / cell['name'] / 'query.json')
    for rel in ['data/prompt_data/query.jsonl', 'data/criteria_data/criteria.jsonl',
                'data/test_data/cleaned_data/reference.jsonl']:
        records = [json.loads(line) for line in (prior / 'upstream' / rel).read_text().splitlines() if line.strip()]
        matching = [r for r in records if r['id'] == cell['case_id']]
        if len(matching) != 1 or matching[0]['prompt'] != query['prompt']:
            raise RuntimeError('race_query_reference_or_rubric_mismatch')


def score_completed():
    """Called by the existing supervisor's fresh measurement subprocess, no restart."""
    import fcntl

    request = OUT / 'race-request.json'
    if not request.exists() or not read(request).get('enabled'):
        return
    race = OUT / 'local-storage' / 'race'
    race.mkdir(exist_ok=True)
    with (race / 'score.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = prepare()
        driver_path = OUT / 'profiles/10000/driver/experiment.py'
        assert sha(driver_path) == manifest['profiles']['10000']['driver_sha256']
        driver = load('accepted_race_driver', driver_path)
        driver.ROOT = REPO
        driver.OUT = OUT
        driver.cell_directory = lambda cell: OUT / 'local-storage' / 'runs' / cell['name']
        for cell in manifest['cells']:
            observation = OUT / 'local-storage' / 'runs' / cell['name'] / 'observation.json'
            if not observation.exists() or not read(observation).get('words'):
                continue
            row = read(observation)
            report = observation.parent / 'evaluated-report.md'
            if sha(report) != row['report_sha256']:
                raise RuntimeError('race_report_hash_mismatch')
            target = race / cell['name']
            target.mkdir(exist_ok=True)
            result_path = target / 'result.json'
            if not result_path.exists() and not (target / 'attempt.json').exists():
                validate_race_inputs(cell)
                save(target / 'attempt.json', dict(report_sha256=row['report_sha256'],
                     started_at=dt.datetime.now(dt.timezone.utc).isoformat()))
                save(race / 'status.json', dict(status='scoring', current=cell['name']))
                try:
                    driver.race_score(cell)
                except Exception as error:
                    # Do not let an independent judge failure abort native generation.
                    # Preserve call checkpoints; a previous attempt is never auto-restarted.
                    save(target / 'error.json', dict(error_type=type(error).__name__,
                         report_sha256=row['report_sha256'], automatic_retry=False))
            if result_path.exists():
                result = read(result_path)
                if result['report_sha256'] != row['report_sha256']:
                    raise RuntimeError('race_result_hash_mismatch')
                calls = [read(p) for p in (target / 'calls').glob('*.json')
                         if not p.name.endswith(('.pending.json', '.error.json'))]
                usage = sum(c.get('usage', {}).get('total_tokens', 0) for c in calls)
                row.update(race_status='completed', race_scores=result['scores'],
                           race_overall_percent=result['scores']['overall_score'] * 100,
                           race_tokens=usage)
            else:
                row.update(race_status='attempt_incomplete_requires_review', race_tokens=None)
            save(observation, row)
        rows = summary()
        compact = [{k:row.get(k) for k in ['name', 'case_id', 'target_words', 'words',
                   'generation_tokens', 'race_overall_percent', 'race_tokens', 'race_status']}
                   for row in rows]
        save(race / 'summary.json', compact)
        lines = ['# 新题三档：Token与RACE', '',
                 '生成Token含缓存输入；评分Token独立记录。RACE沿用固定官方流程与DeepSeek评委，非官方榜单，FACT未运行。', '',
                 '| 条件 | 字数 | 生成Token | RACE | 评分Token |', '|---|---:|---:|---:|---:|']
        for row in compact:
            score = row['race_overall_percent']
            display = f'{score:.2f}' if score is not None else '待评分'
            lines.append(f"| {row['name']} | {row['words']} | {row['generation_tokens']} | {display} | {row['race_tokens']} |")
        (race / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
        save(race / 'status.json', dict(status='waiting_for_reports_or_complete',
             scored=sum(row.get('race_status') == 'completed' for row in rows), total=9))


def start():
    import fcntl
    with (OUT / 'local-storage' / 'supervisor.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = prepare()
        check = preflight()
        if not check['ready']:
            save(OUT / 'local-storage' / 'status.json', dict(status='blocked_preflight', details=check, model_generation_started=False))
            summary()
            print('BLOCKED: model/search endpoints unavailable or credentials missing; no model calls started.')
            return 2
        # Sequential cells preserve the established execution protocol and avoid load changes.
        native = native_for(20000)
        for cell in manifest['cells']:
            directory = OUT / 'local-storage' / 'runs' / cell['name']
            if (directory / 'observation.json').exists():
                continue
            if (directory / 'attempt.json').exists():
                raise RuntimeError('existing_attempt_refuse_automatic_restart: ' + cell['name'])
            save(directory / 'attempt.json', dict(started_at=dt.datetime.now(dt.timezone.utc).isoformat()))
            save(OUT / 'local-storage' / 'status.json', dict(status='running', current=cell['name']))
            with (directory / 'worker.log').open('w') as log:
                result = native.run_worker_process(
                    [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', cell['name']],
                    directory=directory, log=log, timeout_seconds=TIMEOUT + 90)
            save(directory / 'process-result.json', result)
            import subprocess
            subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--measure', cell['name']], check=True)
            summary()
            observation = read(directory / 'observation.json')
            if result['returncode'] or result['stop_reason'] or observation['words'] is None:
                save(OUT / 'local-storage' / 'status.json', dict(status='stopped_execution_failure', current=cell['name']))
                return 3
        save(OUT / 'local-storage' / 'status.json', dict(status='completed', completed_cells=len(manifest['cells'])))
        return 0


if __name__ == '__main__':
    configure_storage()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--worker')
    parser.add_argument('--measure')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--score-completed', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.measure:
        row = measure(args.measure)
        save(OUT / 'local-storage' / 'runs' / args.measure / 'observation.json', row)
        try:
            score_completed()
        except Exception as error:
            save(OUT / 'race/hook-error.json', dict(error_type=type(error).__name__,
                 automatic_retry=False, generation_measurement_saved=True))
    elif args.score_completed:
        score_completed()
    elif args.start:
        sys.exit(start())
    elif args.summary:
        summary()
    else:
        prepare()
        summary()
        print('Prepared 9 cells and verified immutable source snapshots. No model calls started.')
