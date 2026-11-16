# Generated tables (make_figures.py)

## t1_slots

| iterations | cs2 data-local s | cs2 heft s | disagg2 fifo-hash s | cs2 over disagg2 per repeat | cs2 over disagg2 median | cs2 data-local over disagg2 per repeat | cs2 data-local over disagg2 median | cs2 GB moved | disagg2 GB moved | runs |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 15.2 | 11.6 | 64.2 | 4.82, 6.39 | 5.6 | 3.60, 5.01 | 4.31 | 0.13 | 21.47 | 6 |
| 64 | 16.2 | 12.9 | 65.8 | 4.88, 5.37 | 5.13 | 3.65, 4.59 | 4.12 | 0.07 | 21.47 | 6 |
| 256 | 31.2 | 31.1 | 71.8 | 2.21, 2.52 | 2.36 | 2.11, 2.52 | 2.32 | 0.0 | 21.47 | 6 |
| 1024 | 97.6 | 95.0 | 118.9 | 1.29, 1.22 | 1.25 | 1.29, 1.15 | 1.22 | 0.0 | 21.47 | 6 |
| 4096 | 364.8 | 362.6 | 392.4 | 1.08 | 1.08 | 1.08 | 1.08 | 0.0 | 21.47 | 3 |

## t1_sweep

| iterations | cs heft s | cs data-local s | disagg fifo-hash s | disagg heft s | cs over disagg per repeat | cs over disagg median | cs GB moved | disagg GB moved | runs |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 11.1 | 12.1 | 72.3 | 71.4 | 6.46, 6.42, 6.43 | 6.43 | 0.2 | 21.47 | 12 |
| 4 | 11.3 | 13.4 | 73.1 | 73.7 | 6.41, 6.47, 6.35 | 6.41 | 0.2 | 21.47 | 12 |
| 16 | 13.2 | 13.7 | 74.4 | 73.4 | 5.86, 4.82, 5.90 | 5.86 | 0.2 | 21.47 | 12 |
| 64 | 20.7 | 19.9 | 80.2 | 79.5 | 4.56, 3.59, 4.03 | 4.03 | 0.47 | 21.47 | 12 |
| 256 | 60.3 | 47.9 | 105.1 | 105.7 | 2.45, 2.11, 2.20 | 2.2 | 2.65 | 21.47 | 12 |
| 1024 | 164.1 | 142.6 | 203.1 | 204.6 | 1.44, 1.42, 1.42 | 1.42 | 4.03 | 21.47 | 12 |
| 4096 | 560.0 | 540.0 | 595.4 | 594.0 | 1.12, 1.10, 1.10 | 1.1 | 4.03 | 21.47 | 12 |

## t1_overlap

| iterations | cs data-local s | cs heft s | disagg fifo-hash s | disagg2 fifo-hash s | cs over disagg per repeat | cs over disagg median | cs over disagg2 per repeat | cs over disagg2 median | cs GB moved | disagg GB moved | disagg2 GB moved | runs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 14.0 | 13.6 | 71.4 | 64.7 | 5.54, 5.05 | 5.3 | 4.78, 4.79 | 4.79 | 0.27 | 21.47 | 21.47 | 8 |
| 16 | 15.2 | 14.4 | 74.2 | 65.3 | 5.35, 4.97 | 5.16 | 4.61, 4.46 | 4.54 | 0.2 | 21.47 | 21.47 | 8 |
| 64 | 20.7 | 23.5 | 81.8 | 68.3 | 4.03, 3.89 | 3.96 | 3.40, 3.21 | 3.31 | 0.47 | 21.47 | 21.47 | 8 |
| 256 | 47.5 | 61.0 | 107.2 | 73.4 | 2.25, 2.26 | 2.26 | 1.58, 1.51 | 1.55 | 2.62 | 21.47 | 21.47 | 8 |
| 1024 | 144.0 | 168.8 | 206.9 | 119.8 | 1.44, 1.43 | 1.44 | 0.83, 0.84 | 0.83 | 4.03 | 21.47 | 21.47 | 8 |
| 4096 | 540.3 | 557.1 | 596.8 | 390.5 | 1.11, 1.10 | 1.1 | 0.75, 0.70 | 0.72 | 4.03 | 21.47 | 21.47 | 8 |

## t2_droid_batches

| batch | source GB | tasks | cs data-local s | cs beam-4 s | beam-4 planning s | disagg heft s | speedup | data-local GB moved | beam-4 GB moved | heft GB moved | data-local s per source GB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 74.8 | 9704 | 767.7 | 952.0 | 81.9 | 3203.1 | 4.17 | 1.07 | 3.31 | 620.6 | 10.26 |
| 1 | 70.3 | 9132 | 756.8 | 873.6 | 75.0 | 2971.6 | 3.93 | 0.89 | 2.95 | 583.9 | 10.76 |
| 2 | 69.8 | 9020 | 787.8 | 860.8 | 80.1 | 2947.1 | 3.74 | 1.1 | 3.43 | 577.2 | 11.29 |
| 3 | 73.4 | 9484 | 844.2 | 928.4 | 83.7 | 3099.8 | 3.67 | 1.0 | 3.35 | 608.4 | 11.51 |
| 4 | 71.7 | 9196 | 829.1 | 906.3 | 80.4 | 3059.2 | 3.69 | 0.72 | 3.47 | 591.8 | 11.56 |
| 5 | 71.3 | 9244 | 837.5 | 923.5 | 82.9 | 3026.5 | 3.61 | 0.6 | 3.21 | 589.1 | 11.75 |
| 6 | 64.9 | 8364 | 772.2 | 847.4 | 67.0 | 2751.6 | 3.56 | 0.82 | 3.44 | 536.0 | 11.9 |

## t2_droid_totals

| batches | source GB | tasks | cs data-local s | cs data-local h | cs beam-4 s | beam-4 planning s | disagg heft s | disagg heft h | speedup | data-local GB moved | beam-4 GB moved | heft GB moved | cs node-hours | baseline node-hours | node-hour ratio | speedup at batch 0 s per GB (estimate) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7 | 496.2 | 64144 | 5595.3 | 1.55 | 6292.0 | 551.0 | 21058.9 | 5.85 | 3.76 | 6.2 | 23.2 | 4107.0 | 12.4 | 93.6 | 7.53 | 4.14 |

## t2_droid_fused

| pipeline | tasks | cs s | cs2 s | disagg2 s | disagg2 / cs2 | cs2 GB moved | disagg2 GB moved | stage A cs s | stage A disagg heft s | stage A disagg2 s | stage A disagg heft / cs | stage A disagg2 / cs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| unfused | 352 | 32.9 | 21.2 | 91.0 | 4.3 | 0.69 | 22.28 | 31.8 | 115.9 | 94.7 | 3.64 | 2.97 |
| fused | 88 | 22.4 | 13.1 | 23.5 | 1.79 | 0.08 | 4.67 | 22.6 | 36.8 | 22.5 | 1.63 | 0.99 |

## t3_scale

| iterations | source GB | tasks | cs data-local s | cs heft s | disagg fifo-hash s | speedup best cs | speedup data-local | cs data-local GB moved | cs heft GB moved | disagg GB moved |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 274.9 | 4096 | 233.8 | 227.8 | 1139.3 | 5.0 | 4.87 | 2.9 | 4.9 | 343.6 |
| 256 | 274.9 | 4096 | 736.9 | 1175.3 | 1631.9 | 2.21 | 2.21 | 0.9 | 157.2 | 343.6 |

## t4_sched_skew_cells

| policy | iterations | median s | min s | max s | GB moved | planning s | runs |
|---|---|---|---|---|---|---|---|
| heft-cc | 1 | 18.3 | 18.2 | 18.9 | 2.35 | 0.05 | 3 |
| heft-cc | 16 | 20.2 | 19.5 | 20.7 | 2.62 | 0.05 | 3 |
| heft-cc | 128 | 35.1 | 35.1 | 35.2 | 3.02 | 0.05 | 3 |
| heft-cc | 1024 | 95.4 | 95.3 | 96.2 | 4.5 | 0.06 | 3 |
| backlog | 1 | 17.6 | 17.2 | 19.9 | 0.67 | 0.01 | 3 |
| backlog | 16 | 22.2 | 19.4 | 22.4 | 0.87 | 0.01 | 3 |
| backlog | 128 | 38.8 | 36.4 | 39.1 | 1.34 | 0.01 | 3 |
| backlog | 1024 | 108.3 | 108.0 | 108.7 | 2.68 | 0.01 | 3 |
| lp-plan | 1 | 20.2 | 20.0 | 21.0 | 0.81 | 0.04 | 3 |
| lp-plan | 16 | 24.0 | 24.0 | 24.1 | 0.94 | 0.04 | 3 |
| lp-plan | 128 | 43.3 | 43.3 | 43.6 | 1.61 | 0.04 | 3 |
| lp-plan | 1024 | 114.9 | 112.8 | 115.7 | 3.22 | 0.03 | 3 |
| greedy-wait | 1 | 20.6 | 20.2 | 21.9 | 0.0 | 14.65 | 3 |
| greedy-wait | 16 | 27.5 | 25.3 | 28.3 | 0.0 | 17.87 | 3 |
| greedy-wait | 128 | 54.6 | 53.7 | 55.2 | 0.0 | 21.27 | 3 |
| greedy-wait | 1024 | 103.3 | 102.9 | 105.3 | 3.83 | 26.37 | 3 |
| stealing | 1 | 21.9 | 21.8 | 23.1 | 0.87 | 0.08 | 3 |
| stealing | 16 | 23.4 | 22.9 | 24.6 | 0.94 | 0.08 | 3 |
| stealing | 128 | 40.1 | 38.5 | 40.6 | 1.48 | 0.08 | 3 |
| stealing | 1024 | 107.8 | 104.0 | 108.7 | 2.75 | 0.08 | 3 |
| data-local | 1 | 23.2 | 22.6 | 24.2 | 1.07 | 0.02 | 3 |
| data-local | 16 | 22.5 | 22.2 | 24.8 | 1.34 | 0.02 | 3 |
| data-local | 128 | 39.3 | 37.8 | 39.5 | 1.68 | 0.02 | 3 |
| data-local | 1024 | 100.1 | 97.8 | 104.2 | 2.89 | 0.02 | 3 |
| beam-4 | 1 | 27.5 | 26.6 | 30.9 | 1.74 | 0.49 | 3 |
| beam-4 | 16 | 34.8 | 32.0 | 35.9 | 1.95 | 0.5 | 3 |
| beam-4 | 128 | 37.7 | 37.5 | 38.3 | 1.61 | 0.45 | 3 |
| beam-4 | 1024 | 102.1 | 101.6 | 102.2 | 3.02 | 0.43 | 3 |
| heft | 1 | 45.5 | 44.5 | 45.7 | 2.68 | 0.01 | 3 |
| heft | 16 | 39.2 | 39.0 | 39.3 | 3.83 | 0.02 | 3 |
| heft | 128 | 56.4 | 55.9 | 56.6 | 3.89 | 0.02 | 3 |
| heft | 1024 | 108.1 | 107.7 | 109.1 | 4.43 | 0.02 | 3 |
| lp-plan2 (other session) | 1 | 12.8 | 12.7 | 13.8 | 0.47 | 0.05 | 3 |
| lp-plan2 (other session) | 16 | 16.0 | 15.9 | 16.4 | 0.6 | 0.06 | 3 |
| lp-plan2 (other session) | 128 | 33.4 | 33.1 | 33.7 | 1.34 | 0.06 | 3 |
| lp-plan2 (other session) | 1024 | 88.6 | 88.0 | 90.0 | 3.02 | 0.04 | 3 |

## t4_sched_skew

| policy | i1 | i16 | i128 | i1024 |
|---|---|---|---|---|
| heft-cc | 18.3 [18.2-18.9], 2.35 GB | **20.2 [19.5-20.7], 2.62 GB** | **35.1 [35.1-35.2], 3.02 GB** | **95.4 [95.3-96.2], 4.5 GB** |
| backlog | **17.6 [17.2-19.9], 0.67 GB** | 22.2 [19.4-22.4], 0.87 GB | 38.8 [36.4-39.1], 1.34 GB | 108.3 [108.0-108.7], 2.68 GB |
| lp-plan | 20.2 [20.0-21.0], 0.81 GB | 24.0 [24.0-24.1], 0.94 GB | 43.3 [43.3-43.6], 1.61 GB | 114.9 [112.8-115.7], 3.22 GB |
| greedy-wait | 20.6 [20.2-21.9], 0.0 GB | 27.5 [25.3-28.3], 0.0 GB | 54.6 [53.7-55.2], 0.0 GB | 103.3 [102.9-105.3], 3.83 GB |
| stealing | 21.9 [21.8-23.1], 0.87 GB | 23.4 [22.9-24.6], 0.94 GB | 40.1 [38.5-40.6], 1.48 GB | 107.8 [104.0-108.7], 2.75 GB |
| data-local | 23.2 [22.6-24.2], 1.07 GB | 22.5 [22.2-24.8], 1.34 GB | 39.3 [37.8-39.5], 1.68 GB | 100.1 [97.8-104.2], 2.89 GB |
| beam-4 | 27.5 [26.6-30.9], 1.74 GB | 34.8 [32.0-35.9], 1.95 GB | 37.7 [37.5-38.3], 1.61 GB | 102.1 [101.6-102.2], 3.02 GB |
| heft | 45.5 [44.5-45.7], 2.68 GB | 39.2 [39.0-39.3], 3.83 GB | 56.4 [55.9-56.6], 3.89 GB | 108.1 [107.7-109.1], 4.43 GB |
| lp-plan2 (other session) | 12.8 [12.7-13.8], 0.47 GB | 16.0 [15.9-16.4], 0.6 GB | 33.4 [33.1-33.7], 1.34 GB | 88.6 [88.0-90.0], 3.02 GB |

## t4_sched_uniform_cells

| policy | iterations | median s | min s | max s | GB moved | planning s | runs |
|---|---|---|---|---|---|---|---|
| heft-cc | 256 | 43.7 | 43.6 | 43.8 | 0.0 | 0.08 | 2 |
| heft-cc | 1024 | 141.6 | 141.5 | 141.8 | 0.0 | 0.08 | 2 |
| data-local | 256 | 43.9 | 43.4 | 44.3 | 0.07 | 0.04 | 2 |
| data-local | 1024 | 141.8 | 141.4 | 142.1 | 0.0 | 0.04 | 2 |
| backlog | 256 | 45.5 | 44.4 | 46.6 | 0.0 | 0.02 | 2 |
| backlog | 1024 | 142.8 | 142.4 | 143.3 | 0.0 | 0.02 | 2 |
| lp-plan | 256 | 46.5 | 44.8 | 48.2 | 0.07 | 0.05 | 2 |
| lp-plan | 1024 | 142.0 | 141.7 | 142.3 | 0.0 | 0.05 | 2 |
| heft | 256 | 60.2 | 59.5 | 60.9 | 5.17 | 0.03 | 2 |
| heft | 1024 | 165.5 | 164.1 | 167.0 | 8.05 | 0.03 | 2 |
| lp-plan2 (other session) | 256 | 45.9 | 45.6 | 46.2 | 0.07 | 0.06 | 2 |

## t4_sched_uniform

| policy | i256 | i1024 |
|---|---|---|
| heft-cc | **43.7 [43.6-43.8], 0.0 GB** | **141.6 [141.5-141.8], 0.0 GB** |
| data-local | 43.9 [43.4-44.3], 0.07 GB | 141.8 [141.4-142.1], 0.0 GB |
| backlog | 45.5 [44.4-46.6], 0.0 GB | 142.8 [142.4-143.3], 0.0 GB |
| lp-plan | 46.5 [44.8-48.2], 0.07 GB | 142.0 [141.7-142.3], 0.0 GB |
| heft | 60.2 [59.5-60.9], 5.17 GB | 165.5 [164.1-167.0], 8.05 GB |
| lp-plan2 (other session) | 45.9 [45.6-46.2], 0.07 GB |  |

## t4_sched_lp_low

| iterations | policy | median s | min s | max s | repeats s | over heft-cc per repeat | over heft-cc median | GB moved | tasks moved off node 0 | planning s | runs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lp-plan2 | 12.8 | 12.7 | 13.8 | 13.8, 12.7, 12.8 | 0.751, 0.668, 0.719 | 0.719 | 0.47 | 7 | 0.055 | 3 |
| 1 | heft-cc | 18.3 | 17.7 | 19.0 | 18.3, 19.0, 17.7 | 1.000, 1.000, 1.000 | 1.0 | 2.35 | 12 | 0.051 | 3 |
| 1 | backlog | 15.8 | 15.7 | 17.3 | 17.3, 15.7, 15.8 | 0.946, 0.828, 0.892 | 0.892 | 0.6 | 9 | 0.01 | 3 |
| 1 | data-local | 21.4 | 18.7 | 22.2 | 18.7, 22.2, 21.4 | 1.023, 1.170, 1.206 | 1.17 | 1.28 | 14 | 0.014 | 3 |
| 16 | lp-plan2 | 16.0 | 15.9 | 16.4 | 15.9, 16.4, 16.0 | 0.843, 0.863, 0.842 | 0.843 | 0.6 | 9 | 0.061 | 3 |
| 16 | heft-cc | 19.0 | 18.9 | 19.0 | 18.9, 19.0, 19.0 | 1.000, 1.000, 1.000 | 1.0 | 2.62 | 13 | 0.051 | 3 |
| 16 | backlog | 19.0 | 18.9 | 20.3 | 20.3, 18.9, 19.0 | 1.076, 0.998, 0.999 | 0.999 | 0.74 | 11 | 0.009 | 3 |
| 16 | data-local | 22.1 | 21.2 | 22.9 | 22.1, 21.2, 22.9 | 1.170, 1.117, 1.203 | 1.17 | 1.34 | 14 | 0.015 | 3 |

## t4_sched_lp_uniform

| iterations | policy | median s | min s | max s | repeats s | over heft-cc per repeat | over heft-cc median | GB moved | tasks moved off node 0 | planning s | runs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 256 | lp-plan2 | 45.9 | 45.6 | 46.2 | 46.2, 45.6 | 0.990, 1.052 | 1.021 | 0.07 | 1.0 | 0.057 | 2 |
| 256 | heft-cc | 45.0 | 43.4 | 46.7 | 46.7, 43.4 | 1.000, 1.000 | 1.0 | 0.0 | 0.0 | 0.081 | 2 |
| 256 | data-local | 44.2 | 43.8 | 44.5 | 43.8, 44.5 | 0.938, 1.026 | 0.982 | 0.1 | 0.0 | 0.04 | 2 |

## t4_sched_lp

| iterations | policy | median s | repeats s | GB moved | planning s | LP bound s |
|---|---|---|---|---|---|---|
| 128 | lp-plan | 41.8 | 41.8, 43.8, 41.3 | 1.61 | 0.037 | 34.6 |
| 128 | lp-plan2 | 33.4 | 33.1, 33.4, 33.7 | 1.34 | 0.063 | 34.6 |
| 128 | heft-cc | 35.7 | 35.3, 36.5, 35.7 | 3.02 | 0.052 | 34.6 |
| 1024 | lp-plan | 114.9 | 114.9, 118.0, 107.3 | 3.22 | 0.033 | 78.1 |
| 1024 | lp-plan2 | 88.6 | 90.0, 88.6, 88.0 | 3.02 | 0.04 | 78.1 |
| 1024 | heft-cc | 95.0 | 94.9, 95.0, 95.2 | 4.5 | 0.057 | 78.1 |

## t5_sched_mcts

| iterations | policy | median s | repeats s | GB moved | planning s | forecast s |
|---|---|---|---|---|---|---|
| 1 | heft-cc | 17.7 | 17.6, 17.8 | 2.35 | 0.05 | 20.2 |
| 1 | data-local | 21.9 | 21.9, 22.0 | 1.04 | 0.02 | 22.7 |
| 1 | beam-4 | 36.0 | 36.3, 35.7 | 2.28 | 0.37 | 31.3 |
| 1 | native-mcts-heuristic-128 | 26.4 | 25.8, 27.1 | 3.48 | 0.52 | 26.6 |
| 128 | heft-cc | 37.1 | 36.8, 37.5 | 3.02 | 0.05 | 37.7 |
| 128 | data-local | 37.3 | 36.2, 38.4 | 1.58 | 0.02 | 40.4 |
| 128 | beam-4 | 37.3 | 36.4, 38.2 | 1.64 | 0.49 | 39.2 |
| 128 | native-mcts-heuristic-128 | 53.5 | 53.8, 53.3 | 4.3 | 0.77 | 49.5 |
| 1024 | heft-cc | 99.0 | 99.8, 98.3 | 4.5 | 0.06 | 93.8 |
| 1024 | data-local | 103.9 | 103.7, 104.1 | 2.82 | 0.02 | 101.8 |
| 1024 | beam-4 | 102.3 | 101.5, 103.1 | 3.02 | 0.5 | 103.7 |
| 1024 | native-mcts-heuristic-128 | 95.4 | 96.6, 94.1 | 5.91 | 0.51 | 95.5 |

## t5_forecast_vs_measured

| campaign | case | policy | forecast s | measured s | error % | runs |
|---|---|---|---|---|---|---|
| sched-skew | skew-i1 | backlog | 20.2 | 17.6 | 14.8 | 3 |
| sched-skew | skew-i16 | backlog | 22.8 | 22.2 | 2.9 | 3 |
| sched-skew | skew-i128 | backlog | 38.9 | 38.8 | 0.2 | 3 |
| sched-skew | skew-i1024 | backlog | 113.5 | 108.3 | 4.8 | 3 |
| sched-skew | skew-i1 | beam-4 | 31.3 | 27.5 | 13.5 | 3 |
| sched-skew | skew-i16 | beam-4 | 32.5 | 34.8 | -6.6 | 3 |
| sched-skew | skew-i128 | beam-4 | 39.2 | 37.7 | 4.0 | 3 |
| sched-skew | skew-i1024 | beam-4 | 103.7 | 102.1 | 1.5 | 3 |
| sched-skew | skew-i1 | data-local | 22.7 | 23.2 | -2.5 | 3 |
| sched-skew | skew-i16 | data-local | 23.7 | 22.5 | 5.6 | 3 |
| sched-skew | skew-i128 | data-local | 40.4 | 39.3 | 2.8 | 3 |
| sched-skew | skew-i1024 | data-local | 101.8 | 100.1 | 1.7 | 3 |
| sched-skew | skew-i1 | greedy-wait | 21.8 | 20.6 | 5.5 | 3 |
| sched-skew | skew-i16 | greedy-wait | 25.7 | 27.5 | -6.6 | 3 |
| sched-skew | skew-i128 | greedy-wait | 55.1 | 54.6 | 1.0 | 3 |
| sched-skew | skew-i1024 | greedy-wait | 103.0 | 103.3 | -0.2 | 3 |
| sched-skew | skew-i1 | heft | 45.2 | 45.5 | -0.7 | 3 |
| sched-skew | skew-i16 | heft | 38.8 | 39.2 | -1.0 | 3 |
| sched-skew | skew-i128 | heft | 55.7 | 56.4 | -1.4 | 3 |
| sched-skew | skew-i1024 | heft | 110.6 | 108.1 | 2.4 | 3 |
| sched-skew | skew-i1 | heft-cc | 20.2 | 18.3 | 10.1 | 3 |
| sched-skew | skew-i16 | heft-cc | 22.8 | 20.2 | 12.8 | 3 |
| sched-skew | skew-i128 | heft-cc | 37.7 | 35.1 | 7.2 | 3 |
| sched-skew | skew-i1024 | heft-cc | 93.8 | 95.4 | -1.7 | 3 |
| sched-skew | skew-i1 | lp-plan | 20.2 | 20.2 | -0.2 | 3 |
| sched-skew | skew-i16 | lp-plan | 22.6 | 24.0 | -5.9 | 3 |
| sched-skew | skew-i128 | lp-plan | 39.1 | 43.3 | -9.8 | 3 |
| sched-skew | skew-i1024 | lp-plan | 110.7 | 114.9 | -3.7 | 3 |
| sched-skew | skew-i1 | stealing | 22.4 | 21.9 | 2.0 | 3 |
| sched-skew | skew-i16 | stealing | 23.0 | 23.4 | -1.9 | 3 |
| sched-skew | skew-i128 | stealing | 40.3 | 40.1 | 0.6 | 3 |
| sched-skew | skew-i1024 | stealing | 113.5 | 107.8 | 5.3 | 3 |
| sched-uniform | uniform-i256 | backlog | 43.7 | 45.5 | -3.9 | 2 |
| sched-uniform | uniform-i1024 | backlog | 143.0 | 142.8 | 0.1 | 2 |
| sched-uniform | uniform-i256 | data-local | 44.1 | 43.9 | 0.6 | 2 |
| sched-uniform | uniform-i1024 | data-local | 143.0 | 141.8 | 0.9 | 2 |
| sched-uniform | uniform-i256 | heft | 55.4 | 60.2 | -8.0 | 2 |
| sched-uniform | uniform-i1024 | heft | 161.7 | 165.5 | -2.3 | 2 |
| sched-uniform | uniform-i256 | heft-cc | 43.7 | 43.7 | 0.0 | 2 |
| sched-uniform | uniform-i1024 | heft-cc | 143.0 | 141.6 | 1.0 | 2 |
| sched-uniform | uniform-i256 | lp-plan | 44.1 | 46.5 | -5.1 | 2 |
| sched-uniform | uniform-i1024 | lp-plan | 143.0 | 142.0 | 0.7 | 2 |
| sched-lp | skew-i128 | lp-plan2 | 39.2 | 33.4 | 17.3 | 3 |
| sched-lp | skew-i1024 | lp-plan2 | 91.6 | 88.6 | 3.4 | 3 |
| sched-mcts | skew-i1 | native-mcts-heuristic-128 | 26.6 | 26.4 | 0.4 | 2 |
| sched-mcts | skew-i128 | native-mcts-heuristic-128 | 49.5 | 53.5 | -7.6 | 2 |
| sched-mcts | skew-i1024 | native-mcts-heuristic-128 | 95.5 | 95.4 | 0.1 | 2 |

## t5_search_simulated

| policy | skew-i1 | skew-i16 | skew-i128 | skew-i1024 | uniform-i256 | uniform-i1024 | mean wall / heft-cc |
|---|---|---|---|---|---|---|---|
| heft-cc | 20.3 | 22.9 | 37.8 | 93.9 | 43.8 | 143.2 | 1.0 |
| data-local | 22.7 | 23.7 | 40.4 | 101.8 | 44.1 | 143.0 | 1.052 |
| beam-4 | 31.3 | 32.6 | 39.3 | 102.6 | 43.8 | 143.1 | 1.183 |
| native-mcts-heuristic-128 | 24.9 | 30.0 | 50.3 | 95.5 | 53.2 | 154.3 | 1.196 |
| native-mcts-fair-128 | 24.7 | 26.2 | 39.3 | 94.4 | 54.0 | 154.5 | 1.12 |
| gumbel-lb-32 | 24.3 | 25.8 | 40.5 | 97.7 | 48.5 | 158.4 | 1.108 |
| restarts-32 | 23.2 | 26.2 | 43.7 | 93.6 | 47.9 | 153.7 | 1.101 |

## t6_netsweep

| link Mbit | iterations | cs2 s | disagg2 s | policies | speedup per repeat | speedup median | cs2 GB moved | disagg2 GB moved | runs | failed verification |
|---|---|---|---|---|---|---|---|---|---|---|
| 400 | 1 | 5.7 | 32.1 | fifo-hash / heft | 4.94, 6.40 | 5.67 | 0.0 | 10.74 | 4 | 0 |
| 1000 | 1 | 5.5 | 13.3 | fifo-hash / heft | 2.12, 2.76 | 2.44 | 0.0 | 10.74 | 4 | 0 |
| 2500 | 1 | 6.0 | 7.5 | fifo-hash / heft | 1.33, 1.21 | 1.27 | 0.0 | 10.74 | 4 | 0 |
| 400 | 256 | 17.0 | 36.9 | fifo-hash / heft | 1.98, 2.40 | 2.19 | 0.0 | 10.74 | 4 | 0 |
| 1000 | 256 | 15.1 | 21.0 | fifo-hash / heft | 1.43, 1.35 | 1.39 | 0.0 | 10.74 | 4 | 0 |
| 2500 | 256 | 16.3 | 18.5 | fifo-hash / heft | 1.12, 1.15 | 1.14 | 0.0 | 10.74 | 4 | 0 |

## t7_sched_droid

| mode | policy | median s | per wave s | over data-local per wave | GB moved per wave | tasks moved off node 0 per wave | planning s per wave | runs |
|---|---|---|---|---|---|---|---|---|
| cs | data-local | 75.9 | 73.8, 78.0 | 1.000, 1.000 | 3.26, 3.37 | 83, 100 | 0.03, 0.03 | 2 |
| cs | heft-cc | 86.6 | 85.6, 87.6 | 1.160, 1.122 | 3.82, 4.29 | 79, 98 | 8.11, 7.92 | 2 |
| cs | lp-plan2 | 93.2 | 86.7, 99.7 | 1.175, 1.278 | 2.50, 2.75 | 81, 96 | 17.42, 23.08 | 2 |
| cs | heft | 86.9 | 87.9, 85.9 | 1.191, 1.101 | 5.15, 4.63 | 93, 106 | 1.19, 1.23 | 2 |
| disagg | fifo-hash | 116.8 | 117.6, 116.1 | 1.594, 1.488 | 11.79, 11.53 |  | 0.01, 0.01 | 2 |

## t7_sched_droid_waves

| wave | dataset | source GB | tasks | node 0 share of source | best cs policy | best cs s | disagg fifo-hash s | disagg over best cs | runs | failed verification | output digests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | droid-128-00500..00507 | 6.91 | 217 | 0.544 | data-local | 73.8 | 117.6 | 1.59 | 5 | 0 | 1 |
| 1 | droid-128-00508..00515 | 6.75 | 224 | 0.62 | data-local | 78.0 | 116.1 | 1.49 | 5 | 0 | 1 |

## checks

| campaign | rows | failed_verification | cells | cells_with_more_than_one_digest |
|---|---|---|---|---|
| demo3-slots | 27 | 0 | 5 | 0 |
| demo3-sweep | 84 | 0 | 7 | 0 |
| demo3-overlap-sweep | 48 | 0 | 6 | 0 |
| demo3-droid | 21 | 0 | 7 | 0 |
| demo3-slots-droid | 6 | 0 | 2 | 0 |
| demo3-sensitivity-droid | 8 | 0 | 2 | 0 |
| demo3-scale | 6 | 0 | 2 | 0 |
| sched-skew | 96 | 0 | 4 | 0 |
| sched-uniform | 20 | 0 | 2 | 0 |
| sched-lp | 18 | 0 | 2 | 0 |
| sched-mcts | 24 | 0 | 3 | 0 |
| sched-droid | 10 | 0 | 2 | 0 |
| sched-lp-low | 24 | 0 | 2 | 0 |
| sched-lp-uniform | 6 | 0 | 1 | 0 |
| netsweep-1000 | 8 | 0 | 2 | 0 |
| netsweep-2500 | 8 | 0 | 2 | 0 |
| netsweep-400 | 8 | 0 | 2 | 0 |
