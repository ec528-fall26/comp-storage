"""A small dense two-phase simplex solver for the placement LP.

The lab's containers run numpy 2.0.2 and carry scipy 1.9.3, which does not import with that
numpy (ImportError: cannot import name 'Inf' from 'numpy'), so the LP is solved here. Bland's rule picks the entering and leaving variables,
which guarantees termination on degenerate problems such as the placement LP, whose
resource rows all have a zero right-hand side.
"""

import numpy as np

TOLERANCE = 1e-9


def minimize(cost, upper, upper_bounds, equal, equal_bounds):
    """x >= 0 minimizing cost @ x subject to upper @ x <= upper_bounds and
    equal @ x == equal_bounds; raises ValueError if there is none or it is unbounded."""
    cost = np.asarray(cost, dtype=float)
    cost = cost / max(1.0, np.abs(cost).max())
    variables = len(cost)
    rows = np.vstack(
        [
            np.asarray(upper, dtype=float).reshape(-1, variables),
            np.asarray(equal, dtype=float).reshape(-1, variables),
        ]
    )
    bounds = np.concatenate([np.asarray(upper_bounds, float), np.asarray(equal_bounds, float)])
    slacks = len(rows) - len(equal_bounds)
    # Columns: the variables, a slack per inequality, an artificial per row, the bounds.
    table = np.zeros((len(rows), variables + slacks + len(rows) + 1))
    table[:, :variables] = rows
    table[:slacks, variables : variables + slacks] = np.eye(slacks)
    table[:, -1] = bounds
    table[bounds < 0] *= -1
    scale = np.abs(table[:, :-1]).max(axis=1)
    table /= np.where(scale > 0, scale, 1)[:, None]
    artificial = variables + slacks
    table[:, artificial:-1] = np.eye(len(rows))
    basis = list(range(artificial, artificial + len(rows)))

    infeasibility = -table.sum(axis=0)
    infeasibility[artificial:-1] = 0
    _pivot_to_optimum(table, basis, infeasibility, artificial)
    if -infeasibility[-1] > TOLERANCE * max(1.0, np.abs(table[:, -1]).max()):
        raise ValueError("the linear program has no feasible point")
    for row in reversed(range(len(basis))):
        if basis[row] >= artificial:
            columns = np.flatnonzero(np.abs(table[row, :artificial]) > TOLERANCE)
            if len(columns):
                _pivot(table, infeasibility, row, columns[0])
                basis[row] = columns[0]
            else:
                table = np.delete(table, row, axis=0)
                del basis[row]
    table = np.delete(table, np.s_[artificial:-1], axis=1)

    objective = np.zeros(table.shape[1])
    objective[:variables] = cost
    for row, column in enumerate(basis):
        objective -= objective[column] * table[row]
    _pivot_to_optimum(table, basis, objective, artificial)
    solution = np.zeros(table.shape[1] - 1)
    solution[basis] = table[:, -1]
    return solution[:variables]


def _pivot_to_optimum(table, basis, objective, columns):
    """Pivot until no reduced cost among the first `columns` columns is negative."""
    while True:
        improving = np.flatnonzero(objective[:columns] < -TOLERANCE)
        if not len(improving):
            return
        entering = improving[0]
        column = table[:, entering]
        positive = column > TOLERANCE
        if not positive.any():
            raise ValueError("the linear program is unbounded")
        ratios = np.full(len(column), np.inf)
        ratios[positive] = np.maximum(table[positive, -1], 0) / column[positive]
        tied = np.flatnonzero(ratios <= ratios.min() + TOLERANCE)
        leaving = min(tied, key=lambda row: basis[row])
        _pivot(table, objective, leaving, entering)
        basis[leaving] = entering


def _pivot(table, objective, row, column):
    table[row] /= table[row, column]
    factors = table[:, column].copy()
    factors[row] = 0
    table -= np.outer(factors, table[row])
    objective -= objective[column] * table[row]
