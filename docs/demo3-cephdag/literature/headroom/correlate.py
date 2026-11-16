"""Does per-task dispatch overhead track network bytes or planning time? Usage: .venv/bin/python -I correlate.py OUT_DIR

Reads OUT_DIR/gaps.csv (gaps.py) and OUT_DIR/runs.csv (headroom.py); one point per run.
"""
import collections
import csv
import sys
from pathlib import Path

from scipy.stats import spearmanr


def main(out_dir):
    out = Path(out_dir)
    runs = collections.defaultdict(lambda: dict(overhead=0.0, tasks=0, planning=0.0))
    for row in csv.DictReader(open(out / 'gaps.csv')):
        run = runs[(row['file'], row['shape'])]
        run['overhead'] += float(row['overhead'])
        run['tasks'] += int(row['tasks'])
        run['planning'] = float(row['planning'])
    remote = {row['file']: float(row['remote_gib']) for row in csv.DictReader(open(out / 'runs.csv'))}
    for shape in ('independent', 'competing'):
        keys = [k for k in runs if k[1] == shape]
        overhead_ms = [1000 * runs[k]['overhead'] / runs[k]['tasks'] for k in keys]
        print(f'{shape}: n={len(keys)} per-task overhead (ms) vs remote GiB: {spearmanr(overhead_ms, [remote[k[0]] for k in keys])}')
        print(f'{shape}: n={len(keys)} per-task overhead (ms) vs planning s: {spearmanr(overhead_ms, [runs[k]["planning"] for k in keys])}')


if __name__ == '__main__':
    main(sys.argv[1])
