| case | mode | policy | wall s | policy s | network payload GB | verified | tasks per worker |
|---|---|---|---|---|---|---|---|
| independent-i128-r0 | cs | greedy-wait | 50.77 | 20.720 | 0.000 | True | 65 9 9 9 9 9 9 9 |
| independent-i128-r0 | cs | stealing | 37.46 | 0.083 | 1.409 | True | 44 11 11 12 11 13 14 12 |
| independent-i128-r0 | cs | beam-4 | 36.32 | 0.461 | 1.812 | True | 45 11 12 12 16 10 10 12 |
| independent-i128-r0 | cs | heft | 55.68 | 0.013 | 3.892 | True | 27 13 13 15 15 15 14 16 |
| independent-i128-r0 | cs | data-local | 37.18 | 0.017 | 1.544 | True | 42 12 12 12 13 12 13 12 |
| independent-i1024-r0 | cs | greedy-wait | 103.78 | 23.643 | 4.027 | True | 23 16 14 14 11 16 16 18 |
| independent-i1024-r0 | cs | heft | 111.08 | 0.013 | 4.429 | True | 17 15 16 16 16 16 16 16 |
| independent-i1024-r0 | cs | stealing | 109.79 | 0.079 | 2.617 | True | 26 14 14 15 15 14 15 15 |
| independent-i1024-r0 | cs | data-local | 98.64 | 0.018 | 2.886 | True | 22 15 15 15 16 15 15 15 |
| independent-i1024-r0 | cs | beam-4 | 105.88 | 0.456 | 3.087 | True | 23 14 15 14 16 15 15 16 |
| independent-i128-r0 | disagg | fifo-hash | 95.11 | 0.005 | 10.737 | True | 17 17 15 15 16 20 9 19 |
| independent-i1024-r0 | disagg | fifo-hash | 136.27 | 0.006 | 10.737 | True | 15 15 17 16 17 15 15 18 |

| case | cs best | s | disagg best | s | speedup cs/disagg |
|---|---|---|---|---|---|
| independent-i128-r0 | beam-4 | 36.32 | fifo-hash | 95.11 | 2.62 |
| independent-i1024-r0 | data-local | 98.64 | fifo-hash | 136.27 | 1.38 |

| step | attempt | seconds |
|---|---|---|
| create | 1 | 20 |
| independent-i128-r0-cs | 1 | 585 |
| independent-i1024-r0-cs | 1 | 743 |
| independent-i128-r0-disagg | 1 | 145 |
| independent-i1024-r0-disagg | 1 | 180 |
| delete | 1 | 1 |

first start to last end: 27.9 min
