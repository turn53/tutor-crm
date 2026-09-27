"""Offline CRM-data rehearsal; writes only report-lab files, never the live database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

module_root = Path(__file__).resolve().parent
if not (module_root/'local_reports.py').is_file():
    module_root = module_root.parent
sys.path.insert(0, str(module_root))
import local_reports


def run(folder, attempts=3):
    folder = Path(folder)
    cases = json.loads((folder/'cases.json').read_text('utf-8'))
    failures = []
    for case in reversed(cases):
        source = case.get('raw_transcript') or case['raw_summary']
        out = folder/(case['id']+'.json')
        checkpoint_file = folder/(case['id']+'-stages.json')
        previous = json.loads(out.read_text('utf-8')).get('analysis',{}) if out.exists() else {}
        if (previous.get('version') == local_reports.VERSION and previous.get('check_scope') == 'facts_and_notes'
            and previous.get('automatic_check') == 'passed' and previous.get('source_hash') == hashlib.sha256(source.encode()).hexdigest()):
            continue
        def checkpoint(work):
            pending = checkpoint_file.with_suffix('.tmp')
            pending.write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')
            pending.replace(checkpoint_file)
        print('START', case['display_name'], flush=True)
        started = time.monotonic()
        for attempt in range(1, attempts+1):
            try:
                report, analysis = local_reports.local_report(source,
                    json.loads(checkpoint_file.read_text('utf-8')) if checkpoint_file.exists() else None, checkpoint)
                analysis['source_kind'] = 'transcript' if case.get('raw_transcript') else 'summary'
                out.write_text(json.dumps({'report': report, 'analysis': analysis, 'attempts':attempt,
                    'run_seconds':round(time.monotonic()-started)}, ensure_ascii=False), encoding='utf-8')
                print('DONE', case['display_name'], round(time.monotonic()-started), json.dumps(report, ensure_ascii=False), flush=True)
                break
            except Exception as exc:
                print('RETRY' if attempt<attempts else 'ERROR', case['display_name'], type(exc).__name__, str(exc), flush=True)
                if not isinstance(exc, local_reports.ReportUnavailable) or attempt==attempts:
                    failures.append(case['id'])
                    break
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--endpoint', default='http://127.0.0.1:8088')
    parser.add_argument('--attempts', type=int, default=3)
    args = parser.parse_args()
    os.environ['CRM_LOCAL_MODEL_URL'] = args.endpoint
    if not 1 <= args.attempts <= 5: parser.error('attempts must be between 1 and 5')
    raise SystemExit(bool(run(args.folder, args.attempts)))
