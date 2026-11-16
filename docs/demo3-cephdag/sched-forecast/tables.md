
fair sharing: forecast wall, bytes moved (measured median wall, bytes)

| policy | skew-i1 | skew-i16 | skew-i128 | skew-i1024 | uniform-i256 | uniform-i1024 |
|---|---|---|---|---|---|---|
| heft-cc | 20.2 s, 2.35 GB | 22.8 s, 2.62 GB | 37.7 s, 3.02 GB | 93.8 s, 4.50 GB | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |
| backlog | 20.2 s, 0.81 GB | 22.8 s, 0.87 GB | 38.9 s, 1.48 GB | 113.5 s, 2.68 GB | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |
| lp-plan | 20.2 s, 0.81 GB | 22.6 s, 0.94 GB | 39.1 s, 1.61 GB | 110.7 s, 3.22 GB | 44.1 s, 0.07 GB | 143.0 s, 0.00 GB |
| data-local | 22.7 s, 1.28 GB (22.0 s, 1.01 GB) | 23.7 s, 1.28 GB (22.2 s, 1.28 GB) | 40.4 s, 1.54 GB (37.7 s, 1.61 GB) | 101.8 s, 2.89 GB (97.0 s, 2.89 GB) | 44.1 s, 0.13 GB (47.9 s, 0.07 GB) | 143.0 s, 0.00 GB (142.6 s, 0.00 GB) |
| greedy-wait | 21.8 s, 0.00 GB (19.1 s, 0.00 GB) | 25.7 s, 0.00 GB (23.5 s, 0.00 GB) | 55.1 s, 0.00 GB (51.4 s, 0.00 GB) | 103.0 s, 3.76 GB (102.0 s, 3.96 GB) | 43.8 s, 0.00 GB | 143.0 s, 0.00 GB |
| stealing | 22.4 s, 0.94 GB (21.4 s, 0.87 GB) | 23.0 s, 0.94 GB (21.9 s, 0.87 GB) | 40.3 s, 1.54 GB (37.6 s, 1.41 GB) | 113.5 s, 2.68 GB (104.5 s, 2.82 GB) | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |
| heft | 45.2 s, 2.68 GB (45.2 s, 2.68 GB) | 38.8 s, 3.83 GB (39.4 s, 3.83 GB) | 55.7 s, 3.89 GB (56.1 s, 3.89 GB) | 110.6 s, 4.43 GB (110.5 s, 4.43 GB) | 55.4 s, 5.17 GB (60.3 s, 5.17 GB) | 161.7 s, 8.05 GB (164.1 s, 8.05 GB) |
| beam-4 | 31.3 s, 2.28 GB (33.4 s, 2.21 GB) | 32.5 s, 2.21 GB (31.6 s, 2.01 GB) | 39.2 s, 1.41 GB (37.6 s, 1.68 GB) | 103.7 s, 3.02 GB (101.2 s, 2.95 GB) | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |

equal sharing: forecast wall, bytes moved (measured median wall, bytes)

| policy | skew-i1 | skew-i16 | skew-i128 | skew-i1024 | uniform-i256 | uniform-i1024 |
|---|---|---|---|---|---|---|
| heft-cc | 27.7 s, 2.35 GB | 30.6 s, 2.62 GB | 46.2 s, 3.02 GB | 96.4 s, 4.50 GB | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |
| backlog | 50.9 s, 2.28 GB | 51.5 s, 2.28 GB | 58.9 s, 2.42 GB | 117.8 s, 2.89 GB | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |
| lp-plan | 25.2 s, 0.81 GB | 28.0 s, 0.94 GB | 42.6 s, 1.61 GB | 110.7 s, 3.22 GB | 44.1 s, 0.07 GB | 143.0 s, 0.00 GB |
| data-local | 53.6 s, 2.48 GB (22.0 s, 1.01 GB) | 53.8 s, 2.48 GB (22.2 s, 1.28 GB) | 59.2 s, 2.48 GB (37.7 s, 1.61 GB) | 109.8 s, 3.02 GB (97.0 s, 2.89 GB) | 44.1 s, 0.13 GB (47.9 s, 0.07 GB) | 143.0 s, 0.00 GB (142.6 s, 0.00 GB) |
| greedy-wait | 21.8 s, 0.00 GB (19.1 s, 0.00 GB) | 25.7 s, 0.00 GB (23.5 s, 0.00 GB) | 55.1 s, 0.00 GB (51.4 s, 0.00 GB) | 106.8 s, 3.76 GB (102.0 s, 3.96 GB) | 43.8 s, 0.00 GB | 143.0 s, 0.00 GB |
| stealing | 52.3 s, 2.35 GB (21.4 s, 0.87 GB) | 52.9 s, 2.35 GB (21.9 s, 0.87 GB) | 58.9 s, 2.42 GB (37.6 s, 1.41 GB) | 117.8 s, 2.89 GB (104.5 s, 2.82 GB) | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |
| heft | 46.6 s, 2.68 GB (45.2 s, 2.68 GB) | 41.0 s, 3.83 GB (39.4 s, 3.83 GB) | 55.7 s, 3.89 GB (56.1 s, 3.89 GB) | 110.6 s, 4.43 GB (110.5 s, 4.43 GB) | 55.5 s, 5.17 GB (60.3 s, 5.17 GB) | 161.7 s, 8.05 GB (164.1 s, 8.05 GB) |
| beam-4 | 53.6 s, 2.35 GB (33.4 s, 2.21 GB) | 54.5 s, 2.35 GB (31.6 s, 2.01 GB) | 60.4 s, 2.89 GB (37.6 s, 1.68 GB) | 105.0 s, 3.22 GB (101.2 s, 2.95 GB) | 43.7 s, 0.00 GB | 143.0 s, 0.00 GB |

| case | LP bound | moved | off node 0 | build | simplex solve | HiGHS solve |
|---|---|---|---|---|---|---|
| skew-i1 | 17.6 s | 0.83 GB | 12 | 13 ms | 13 ms | 3.8 ms |
| skew-i16 | 20.1 s | 0.95 GB | 14 | 12 ms | 12 ms | 1.6 ms |
| skew-i128 | 34.6 s | 1.63 GB | 24 | 12 ms | 14 ms | 1.7 ms |
| skew-i1024 | 78.1 s | 3.19 GB | 48 | 11 ms | 6 ms | 2.0 ms |
| uniform-i256 | 42.6 s | 0.08 GB | 1 | 28 ms | 7 ms | 1.9 ms |
| uniform-i1024 | 141.8 s | 0.03 GB | 0 | 25 ms | 8 ms | 1.8 ms |
