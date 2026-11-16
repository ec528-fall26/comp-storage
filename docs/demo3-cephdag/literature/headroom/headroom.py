"""Makespan lower bounds for the two large cephdag workloads, plus a decomposition of measured runs.

Read-only analysis. Usage: .venv/bin/python -I headroom.py RESULTS_DIR OUT_DIR

The bound is a fluid relaxation of the scheduling problem the policies face: every task class is split
fractionally across workers, every output may be stored on any node, and the makespan T must cover the
busy time of each worker slot and the byte load of each storage and network resource. Real schedules
are integral, contended and pay planning time, so any real schedule is at least T under the same rates.
"""
import collections
import csv
import json
import statistics
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

MiB = 2**20
SHARD = 16 * MiB
NODES = range(3)
SHARD_NODE = (0, 0, 1, 2)
MIN_SAMPLES = 20


def compute_seconds(model, input_sizes, iterations, output_sizes):
    return max(0.0, model['fixed_seconds'] + sum(input_sizes) * model['input_seconds_per_byte']
               + max(input_sizes) * iterations * model['iteration_seconds_per_byte']
               + sum(output_sizes) * model['output_seconds_per_byte'])


def output_size(input_sizes, ratio):
    return int(sum(input_sizes) * ratio / 4) * 4


def independent_case():
    out = output_size([SHARD], 1 / 128)
    classes = {'map': dict(inputs=['source'], iterations=1, output=out, in_sizes=[SHARD])}
    origins = {(SHARD_NODE[i % 4], 'even' if i % 2 == 0 else 'odd'): 0 for i in range(4)}
    for i in range(2048):
        origins[(SHARD_NODE[i % 4], 'even' if i % 2 == 0 else 'odd')] += 1
    return classes, origins, lambda parity, name: classes[name]['iterations']


def competing_case(iterations=8):
    a = output_size([SHARD], 1 / 16)
    c = output_size([a], 1)
    e = output_size([a, c], .25)
    f = output_size([c, e], .25)
    classes = {
        'a': dict(inputs=['source'], output=a, in_sizes=[SHARD]),
        'b': dict(inputs=['source'], output=a, in_sizes=[SHARD]),
        'c': dict(inputs=['a'], output=c, in_sizes=[a]),
        'd': dict(inputs=['b'], output=c, in_sizes=[a]),
        'e': dict(inputs=['a', 'd'], output=e, in_sizes=[a, c]),
        'f': dict(inputs=['c', 'e'], output=f, in_sizes=[c, e]),
    }
    origins = collections.Counter((SHARD_NODE[i % 4], 'even' if i % 2 == 0 else 'odd') for i in range(512))

    def iters(parity, name):
        left, right = (4, 1) if parity == 'even' else (1, 4)
        return iterations * {'a': left, 'b': right, 'c': right, 'd': left, 'e': 1, 'f': 1}[name]
    return classes, dict(origins), iters


class Stage:
    """Isolated stage durations: calibrated model rates, or empirical per-(kind, bytes, locality) floors."""

    def __init__(self, models, empirical=None):
        self.m = models
        self.empirical = empirical

    def net(self, src, dst):
        return min(self.m['network']['tx_bps'][src], self.m['network']['rx_bps'][dst])

    def read(self, nbytes, store, worker):
        if self.empirical:
            return self.empirical[('read', nbytes, store == worker, worker)]
        rate = self.m['storage']['read_bps'][store]
        return nbytes / (rate if store == worker else min(rate, self.net(store, worker)))

    def write(self, nbytes, worker, store):
        if self.empirical:
            return self.empirical[('write', nbytes, store == worker, worker)]
        rate = self.m['storage']['write_bps'][store]
        return nbytes / (rate if store == worker else min(rate, self.net(worker, store)))

    def unstaged(self, worker):
        """Worker-reported task time outside the read/compute/write stages (setup, reservation, publish)."""
        return self.empirical[('unstaged', 0, True, worker)] if self.empirical else 0.0

    def compute(self, in_sizes, iterations, out, worker):
        if self.empirical:
            return self.empirical[('compute', iterations, sum(in_sizes), worker)]
        return compute_seconds(self.m['compute'], in_sizes, iterations, [out])


def lower_bound(classes, origins, iters, stage, shared_link=False):
    """Fluid LP. y[o,k,p]: tasks of class k from origin o run on worker p and store their output on t.
    For each consumer edge u->v, z[o,e,p,t,q] splits (producer p, store t) mass over consumer worker q,
    so reads are charged to the store actually used and writes to the store chosen once per output."""
    var = {}

    def v(key):
        if key not in var:
            var[key] = len(var)
        return var[key]

    T = v('T')
    consumers = collections.defaultdict(list)
    for name, spec in classes.items():
        for j, src in enumerate(spec['inputs']):
            if src != 'source':
                consumers[src].append((name, j))
    for o in origins:
        for k in classes:
            for p in NODES:
                for t in NODES:
                    v(('y', o, k, p, t))
        for u, cons in consumers.items():
            for (cv, j) in cons:
                for p in NODES:
                    for t in NODES:
                        for q in NODES:
                            v(('z', o, u, cv, j, p, t, q))

    resources = [f'{kind}:{n}' for n in NODES for kind in ('slot', 'read', 'write', 'tx', 'rx', 'link')]
    load = {r: collections.defaultdict(float) for r in resources}
    eq_rows = []

    for o, count in origins.items():
        node, parity = o
        for k, spec in classes.items():
            it = iters(parity, k)
            eq_rows.append(({v(('y', o, k, p, t)): 1.0 for p in NODES for t in NODES}, float(count)))
            for p in NODES:
                for t in NODES:
                    y = v(('y', o, k, p, t))
                    load[f'slot:{p}'][y] += stage.compute(spec['in_sizes'], it, spec['output'], p) + stage.unstaged(p)
                    load[f'slot:{p}'][y] += stage.write(spec['output'], p, t)
                    load[f'write:{t}'][y] += spec['output'] / stage.m['storage']['write_bps'][t]
                    if t != p:
                        load[f'tx:{p}'][y] += spec['output'] / stage.m['network']['tx_bps'][p]
                        load[f'rx:{t}'][y] += spec['output'] / stage.m['network']['rx_bps'][t]
                        load[f'link:{p}'][y] += spec['output'] / stage.m['network']['tx_bps'][p]
                        load[f'link:{t}'][y] += spec['output'] / stage.m['network']['rx_bps'][t]
                    for j, src in enumerate(spec['inputs']):
                        if src != 'source':
                            continue
                        nbytes = spec['in_sizes'][j]
                        load[f'slot:{p}'][y] += stage.read(nbytes, node, p)
                        load[f'read:{node}'][y] += nbytes / stage.m['storage']['read_bps'][node]
                        if node != p:
                            load[f'tx:{node}'][y] += nbytes / stage.m['network']['tx_bps'][node]
                            load[f'rx:{p}'][y] += nbytes / stage.m['network']['rx_bps'][p]
                            load[f'link:{node}'][y] += nbytes / stage.m['network']['tx_bps'][node]
                            load[f'link:{p}'][y] += nbytes / stage.m['network']['rx_bps'][p]
        for u, cons in consumers.items():
            nbytes = classes[u]['output']
            for (cv, j) in cons:
                for p in NODES:
                    for t in NODES:
                        eq_rows.append(({v(('z', o, u, cv, j, p, t, q)): 1.0 for q in NODES}
                                        | {v(('y', o, u, p, t)): -1.0}, 0.0))
                for q in NODES:
                    row = {v(('z', o, u, cv, j, p, t, q)): 1.0 for p in NODES for t in NODES}
                    for t in NODES:
                        row[v(('y', o, cv, q, t))] = row.get(v(('y', o, cv, q, t)), 0.0) - 1.0
                    eq_rows.append((row, 0.0))
                for p in NODES:
                    for t in NODES:
                        for q in NODES:
                            z = v(('z', o, u, cv, j, p, t, q))
                            load[f'slot:{q}'][z] += stage.read(nbytes, t, q)
                            load[f'read:{t}'][z] += nbytes / stage.m['storage']['read_bps'][t]
                            if t != q:
                                load[f'tx:{t}'][z] += nbytes / stage.m['network']['tx_bps'][t]
                                load[f'rx:{q}'][z] += nbytes / stage.m['network']['rx_bps'][q]
                                load[f'link:{t}'][z] += nbytes / stage.m['network']['tx_bps'][t]
                                load[f'link:{q}'][z] += nbytes / stage.m['network']['rx_bps'][q]

    active = [r for r in resources if not r.startswith('link')] if not shared_link else \
        [r for r in resources if not r.startswith(('tx', 'rx'))]
    n = len(var)
    A_ub = lil_matrix((len(active), n))
    for i, r in enumerate(active):
        for col, coef in load[r].items():
            A_ub[i, col] += coef
        A_ub[i, T] = -1.0
    A_eq = lil_matrix((len(eq_rows), n))
    b_eq = np.zeros(len(eq_rows))
    for i, (row, rhs) in enumerate(eq_rows):
        for col, coef in row.items():
            A_eq[i, col] += coef
        b_eq[i] = rhs
    cost = np.zeros(n)
    cost[T] = 1.0
    res = linprog(cost, A_ub=A_ub.tocsr(), b_ub=np.zeros(len(active)), A_eq=A_eq.tocsr(), b_eq=b_eq,
                  bounds=[(0, None)] * n, method='highs')
    if res.status != 0:
        raise RuntimeError(res.message)
    x = res.x
    usage = {r: sum(coef * x[col] for col, coef in load[r].items()) for r in active}
    binding = sorted(usage.items(), key=lambda kv: -kv[1])[:4]
    net_bytes = 0.0
    for key, col in var.items():
        if key == 'T' or x[col] < 1e-9:
            continue
        if key[0] == 'y':
            _, o, k, p, t = key
            node = o[0]
            spec = classes[k]
            if t != p:
                net_bytes += x[col] * spec['output']
            if node != p:
                net_bytes += x[col] * sum(size for src, size in zip(spec['inputs'], spec['in_sizes']) if src == 'source')
        else:
            _, o, u, cv, j, p, t, q = key
            if t != q:
                net_bytes += x[col] * classes[u]['output']
    placement = collections.Counter()
    for key, col in var.items():
        if key != 'T' and key[0] == 'y' and x[col] > 1e-6:
            _, o, k, p, t = key
            placement[f'{k} from node{o[0]} -> worker{p} store{t}'] += x[col]
    lower_bound.last_placement = {k: round(v, 1) for k, v in sorted(placement.items())}
    return res.fun, binding, net_bytes


def all_local_bound(classes, origins, iters, stage):
    """Makespan if every task runs on its shard's node with local outputs: the zero-network corner."""
    busy = collections.Counter()
    for (node, parity), count in origins.items():
        for k, spec in classes.items():
            seconds = stage.compute(spec['in_sizes'], iters(parity, k), spec['output'], node) + stage.unstaged(node)
            seconds += sum(stage.read(s, node, node) for s in spec['in_sizes']) + stage.write(spec['output'], node, node)
            busy[node] += count * seconds
    return max(busy.values()), dict(busy)


def task_inputs(shape, index):
    """(class, shard, input producer indices or None) for a task id under the generators in alternatives.py."""
    if shape == 'independent':
        return 'map', index, [None]
    shard, k = divmod(index, 6)
    name = 'abcdef'[k]
    base = 6 * shard
    producers = {'a': [None], 'b': [None], 'c': [base], 'd': [base + 1], 'e': [base, base + 3], 'f': [base + 2, base + 4]}
    return name, shard, producers[name]


def runs(results_dir):
    for path in sorted(Path(results_dir).glob('run-*.json')):
        yield path, json.loads(path.read_text())


def decompose(results_dir, out_dir):
    rows = []
    stage_samples = collections.defaultdict(list)
    for path, r in runs(results_dir):
        shape = r['config']['shape']
        tasks = r['tasks']
        busy = collections.Counter()
        kind_seconds = collections.Counter()
        for tid, t in tasks.items():
            index = int(tid[1:])
            name, shard, producers = task_inputs(shape, index)
            res = t['result']
            busy[t['worker']] += res['seconds']
            parity = 'even' if shard % 2 == 0 else 'odd'
            iterations = (independent_case() if shape == 'independent' else competing_case())[2](parity, name)
            reads = [s for s in res['stages'] if s['kind'] == 'read']
            w = t['worker']
            unstaged = res['seconds'] - sum(s['seconds'] for s in res['stages'])
            for key in (('unstaged', 0, True), ('unstaged', 0, True, w)):
                stage_samples[(shape,) + key].append(unstaged)
            for s, producer in zip(reads, producers):
                store = SHARD_NODE[shard % 4] if producer is None else tasks[f't{producer:06d}']['targets'][0]
                for key in (('read', s['bytes'], store == w), ('read', s['bytes'], store == w, w)):
                    stage_samples[(shape,) + key].append(s['seconds'])
            for s in res['stages']:
                kind_seconds[s['kind']] += s['seconds']
                if s['kind'] == 'compute':
                    for key in (('compute', iterations, s['bytes']), ('compute', iterations, s['bytes'], w)):
                        stage_samples[(shape,) + key].append(s['seconds'])
                if s['kind'] == 'write':
                    local = t['targets'][0] == w
                    for key in (('write', s['bytes'], local), ('write', s['bytes'], local, w)):
                        stage_samples[(shape,) + key].append(s['seconds'])
        assert len(tasks) == (2048 if shape == 'independent' else 3072), path
        rows.append(dict(file=path.name, shape=shape, scheduler=r['scheduler'], wall=r['wall_seconds'],
                         planning=r['scheduler_seconds'], max_worker_busy=max(busy.values()),
                         min_worker_busy=min(busy.values()), sum_worker_busy=sum(busy.values()),
                         read=kind_seconds['read'], compute=kind_seconds['compute'], write=kind_seconds['write'],
                         remote_gib=r['application_remote_bytes'] / 2**30, waits=r['waits']))
    with open(Path(out_dir) / 'runs.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return rows, stage_samples


def empirical_floors(stage_samples, shape, quantile=0.1):
    floors = {}
    for (s, *key), values in stage_samples.items():
        if s == shape and len(values) >= MIN_SAMPLES:
            floors[tuple(key)] = float(np.quantile(values, quantile))
    return floors


def main(results_dir, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    first = next(runs(results_dir))[1]
    models = first['models']
    rows, samples = decompose(results_dir, out)

    report = {'models_calibration_id': models.get('calibration_id'), 'cases': {}}
    for shape, (classes, origins, iters) in {'independent': independent_case(), 'competing': competing_case()}.items():
        model_stage = Stage(models)
        case = {}
        for label, shared in (('separate_tx_rx', False), ('shared_link', True)):
            t, binding, net = lower_bound(classes, origins, iters, model_stage, shared)
            case[f'model_lb_{label}'] = dict(seconds=t, binding=binding, network_gib=net / 2**30,
                                             placement=lower_bound.last_placement)
        case['model_all_local'] = all_local_bound(classes, origins, iters, model_stage)
        for quantile in (0.1, 0.5):
            floors = empirical_floors(samples, shape, quantile)
            q = int(quantile * 100)
            case.update(empirical_bounds(classes, origins, iters, models, floors, q))
        by_policy = collections.defaultdict(list)
        for row in rows:
            if row['shape'] == shape:
                by_policy[row['scheduler']].append(row)
        case['measured_medians'] = {
            p: {key: statistics.median(r[key] for r in rs)
                for key in ('wall', 'planning', 'max_worker_busy', 'min_worker_busy', 'sum_worker_busy',
                            'read', 'compute', 'write', 'remote_gib')} | {'n': len(rs)}
            for p, rs in sorted(by_policy.items(), key=lambda kv: statistics.median(r['wall'] for r in kv[1]))}
        report['cases'][shape] = case
    (out / 'headroom.json').write_text(json.dumps(report, indent=1, default=str))
    for shape, case in report['cases'].items():
        print(f'== {shape}')
        for key in ('model_lb_separate_tx_rx', 'model_lb_shared_link', 'empirical_p10_lb_separate_tx_rx',
                    'empirical_p10_lb_shared_link', 'empirical_p50_lb_separate_tx_rx', 'empirical_p50_lb_shared_link'):
            c = case[key]
            print(f'  {key:34s} {c["seconds"]:7.2f} s  net {c["network_gib"]:.3f} GiB  binding '
                  + ', '.join(f'{r}={u:.1f}' for r, u in c['binding']))
        for key in ('model_all_local', 'empirical_p10_all_local', 'empirical_p50_all_local'):
            print(f'  {key:34s} {case[key][0]:7.2f} s  per node ' + ', '.join(f'{v:.1f}' for v in case[key][1].values()))
        print('  missing empirical keys:', case.get('empirical_p10_missing_keys'), case.get('empirical_p50_missing_keys'))
        for p, m in case['measured_medians'].items():
            print(f'  {p:26s} wall {m["wall"]:7.2f} plan {m["planning"]:6.2f} busy max {m["max_worker_busy"]:6.2f}'
                  f' min {m["min_worker_busy"]:6.2f} sum {m["sum_worker_busy"]:6.1f} | read {m["read"]:6.1f}'
                  f' comp {m["compute"]:5.1f} write {m["write"]:5.1f} | remote {m["remote_gib"]:.3f} GiB n={m["n"]}')


def empirical_bounds(classes, origins, iters, models, floors, q):
    missing = set()

    class Empirical(dict):
        """Per-worker floor when that worker has enough samples, else the pooled floor, else (compute only)
        the cheapest pooled floor with the same iteration count; anything else counts as free and is reported."""
        def __missing__(self, key):
            if len(key) == 4:
                pooled = key[:3]
                if pooled in floors:
                    return floors[pooled]
                if key[0] == 'compute':
                    alt = [v for k, v in floors.items() if len(k) == 3 and k[0] == 'compute' and k[1] == key[1]]
                    if alt:
                        return min(alt)
            missing.add(key)
            return 0.0
    stage = Stage(models, Empirical(floors))
    case = {}
    for label, shared in (('separate_tx_rx', False), ('shared_link', True)):
        t, binding, net = lower_bound(classes, origins, iters, stage, shared)
        case[f'empirical_p{q}_lb_{label}'] = dict(seconds=t, binding=binding, network_gib=net / 2**30,
                                                   placement=lower_bound.last_placement)
    case[f'empirical_p{q}_all_local'] = all_local_bound(classes, origins, iters, stage)
    case[f'empirical_p{q}_floors'] = {str(k): v for k, v in sorted(floors.items(), key=str)}
    case[f'empirical_p{q}_missing_keys'] = sorted(map(str, missing))
    return case


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
