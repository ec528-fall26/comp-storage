"""Which task classes cross the network, per policy? Usage: .venv/bin/python -I remote_mix.py RESULTS_DIR

Competing graph (experiments/alternatives.py): per shard i, tasks 6i..6i+5 are a, b (read the 16 MiB shard),
c (a), d (b), e (a, d), f (c, e). Independent: task i reads shard i. Shard i lives on node (0, 0, 1, 2)[i % 4].
"""
import collections
import json
import statistics
import sys
from pathlib import Path

SHARD_NODE = (0, 0, 1, 2)
PRODUCERS = {'a': [], 'b': [], 'c': [0], 'd': [1], 'e': [0, 3], 'f': [2, 4]}


def remote_mix(run):
    tasks = run['tasks']
    mib = collections.Counter()
    for tid, task in tasks.items():
        index = int(tid[1:])
        if run['config']['shape'] == 'independent':
            name, shard, producers = 'map', index, []
        else:
            shard, k = divmod(index, 6)
            name = 'abcdef'[k]
            producers = [6 * shard + j for j in PRODUCERS[name]]
        stores = [SHARD_NODE[shard % 4]] if not producers else [tasks[f't{p:06d}']['targets'][0] for p in producers]
        reads = [s for s in task['result']['stages'] if s['kind'] == 'read']
        for stage, store in zip(reads, stores):
            if store != task['worker']:
                mib[f'read:{name}'] += stage['bytes'] / 2**20
        for stage, target in zip([s for s in task['result']['stages'] if s['kind'] == 'write'], task['targets']):
            if target != task['worker']:
                mib[f'write:{name}'] += stage['bytes'] / 2**20
        if name in ('a', 'b', 'map') and task['worker'] != SHARD_NODE[shard % 4]:
            mib[f'moved-source-tasks:{name}'] += 1
    return mib


def main(results_dir):
    groups = collections.defaultdict(list)
    for path in sorted(Path(results_dir).glob('run-*.json')):
        run = json.loads(path.read_text())
        groups[(run['config']['shape'], run['scheduler'])].append(remote_mix(run))
    for (shape, scheduler), mixes in sorted(groups.items()):
        keys = sorted(set().union(*mixes))
        summary = ', '.join(f'{k}={statistics.median(m[k] for m in mixes):.0f}' for k in keys)
        print(f'{shape:11s} {scheduler:26s} MiB/count medians: {summary}')


if __name__ == '__main__':
    main(sys.argv[1])
