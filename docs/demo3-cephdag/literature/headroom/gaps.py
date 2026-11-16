"""Where does wall time go on each worker? Usage: .venv/bin/python -I gaps.py RESULTS_DIR OUT_CSV

For each run and worker: busy = sum of worker-reported task seconds; overhead = (actual_finish - submitted)
minus busy, i.e. dispatch, RPC and completion-notice latency per task; gap = time between one task's
actual_finish and the next submit on that worker (coordinator decided nothing for it); head = first
submit; tail = wall - last actual_finish. head + busy + overhead + gaps + tail = wall for each worker.
"""
import collections
import csv
import json
import statistics
import sys
from pathlib import Path


def per_worker(run):
    by_worker = collections.defaultdict(list)
    for task in run['tasks'].values():
        by_worker[task['worker']].append(task)
    rows = []
    for worker, tasks in sorted(by_worker.items()):
        tasks.sort(key=lambda t: t['submitted'])
        busy = sum(t['result']['seconds'] for t in tasks)
        overhead = sum(t['actual_finish'] - t['submitted'] - t['result']['seconds'] for t in tasks)
        gaps = sum(max(0.0, b['submitted'] - a['actual_finish']) for a, b in zip(tasks, tasks[1:]))
        overlap = sum(max(0.0, a['actual_finish'] - b['submitted']) for a, b in zip(tasks, tasks[1:]))
        rows.append(dict(worker=worker, tasks=len(tasks), head=tasks[0]['submitted'], busy=busy, overhead=overhead,
                         gaps=gaps, overlap=overlap, tail=run['wall_seconds'] - max(t['actual_finish'] for t in tasks)))
    return rows


def main(results_dir, out_csv):
    rows = []
    for path in sorted(Path(results_dir).glob('run-*.json')):
        run = json.loads(path.read_text())
        for row in per_worker(run):
            rows.append(dict(file=path.name, shape=run['config']['shape'], scheduler=run['scheduler'],
                             wall=run['wall_seconds'], planning=run['scheduler_seconds'], **row))
    with open(out_csv, 'w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(row['shape'], row['scheduler'], row['worker'])].append(row)
    keys = ('wall', 'planning', 'tasks', 'head', 'busy', 'overhead', 'gaps', 'tail')
    print('shape scheduler worker ' + ' '.join(keys) + '  (medians over repeats)')
    for (shape, scheduler, worker), rs in sorted(groups.items()):
        if scheduler not in ('beam-4', 'heft', 'stealing', 'greedy-wait', 'greedy'):
            continue
        med = {k: statistics.median(r[k] for r in rs) for k in keys}
        print(f'{shape:11s} {scheduler:11s} w{worker} ' + ' '.join(f'{med[k]:7.2f}' for k in keys))
    per_task = collections.defaultdict(list)
    for row in rows:
        per_task[(row['shape'], row['scheduler'])].append(row['overhead'] / row['tasks'])
    print('median per-task dispatch+notice overhead (ms):')
    for key, values in sorted(per_task.items()):
        print(f'  {key[0]:11s} {key[1]:26s} {1000 * statistics.median(values):6.2f}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
