# demo3-skew results (cephdag8, finished 2026-11-13 10:43 EST)

8 GiB of 64 MiB shards placed unevenly: 65 shards on node 0 and 9 on each of nodes 1-7.
Independent workload, ratio 0.25, chunked kernel, tbf 400 Mbit, 3 repeats, cephdag `2b22dd6`.
72 runs, all verified. Raw rows: aakashpc `/mnt/mass/cephdag/campaigns/demo3-skew/*/a1/rows.jsonl`.

Median wall seconds over 3 repeats, [min-max], and median payload moved over the network.

| iterations | 1st | 2nd | 3rd | 4th | 5th | disagg fifo-hash | speedup, best cs |
|---|---|---|---|---|---|---|---|
| 1 | greedy-wait 19.1 [19.1-19.2], 0 GB | stealing 21.4, 0.9 GB | data-local 22.0, 1.0 GB | beam-4 33.4, 2.2 GB | heft 45.2, 2.7 GB | 93.4, 10.7 GB | 4.9x |
| 16 | stealing 21.9 [21.7-22.0], 0.9 GB | data-local 22.2, 1.3 GB | greedy-wait 23.5, 0 GB | beam-4 31.6, 2.0 GB | heft 39.4, 3.8 GB | 94.6, 10.7 GB | 4.3x |
| 128 | beam-4 37.6 [36.3-38.0], 1.7 GB | stealing 37.6, 1.4 GB | data-local 37.7, 1.6 GB | greedy-wait 51.4, 0 GB | heft 56.1, 3.9 GB | 98.9, 10.7 GB | 2.6x |
| 1024 | data-local 97.0 [94.7-98.1], 2.9 GB | beam-4 101.2, 3.0 GB | greedy-wait 102.0, 4.0 GB | stealing 104.5, 2.8 GB | heft 110.5, 4.4 GB | 137.6, 10.7 GB | 1.4x |

Reading. Once compute sits at the storage, where work runs still matters, and the right
amount of movement depends on intensity. At 1 iteration a task's compute is far shorter than
moving its 64 MiB input over node 0's link, so the policy that never moves anything
(greedy-wait) wins and every byte moved costs time. From 128 iterations, keeping everything
on node 0 leaves seven workers idle: greedy-wait is 37% behind, and the policies that move
1.4-1.7 GB off node 0 tie for first. At 1024 every policy moves work and the spread shrinks to
14%. HEFT is last at every intensity: it moves the most (2.7-4.4 GB) because its network model
treats each transfer as if it had node 0's link to itself, while all of them leave through
that one link. Beam-4 also moves too much at low intensity (2.2 GB and 75% behind at
1 iteration; why its forecast favours those moves is not yet checked) but ties for first at 128. This is the contention-aware placement gap in
the 2026-11-12 literature review, measured.
