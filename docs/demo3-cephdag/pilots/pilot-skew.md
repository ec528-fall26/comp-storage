| case | mode | policy | wall s | policy s | network payload GB | verified | tasks per worker |
|---|---|---|---|---|---|---|---|
| independent-i1-r0 | cs | greedy | 23.13 | 5.499 | 2.617 | True | 51 12 11 12 9 12 11 10 |
| independent-i1-r0 | cs | greedy-wait | 17.80 | 13.022 | 0.000 | True | 65 9 9 9 9 9 9 9 |
| independent-i1-r0 | cs | stealing | 18.50 | 0.090 | 0.738 | True | 54 11 11 10 10 11 11 10 |
| independent-i1-r0 | cs | heft | 44.68 | 0.012 | 2.684 | True | 34 14 12 13 14 13 14 14 |
| independent-i1-r0 | cs | data-local | 18.85 | 0.014 | 1.141 | True | 53 10 12 10 10 12 9 12 |
| independent-i1-r0 | cs | beam-4 | 29.22 | 0.428 | 2.215 | True | 53 9 9 10 10 12 15 10 |
| independent-i16-r0 | cs | greedy | 27.50 | 4.174 | 3.355 | True | 47 13 11 10 10 15 9 13 |
| independent-i16-r0 | cs | heft | 60.84 | 0.013 | 3.423 | True | 24 13 14 15 15 15 16 16 |
| independent-i16-r0 | cs | greedy-wait | 24.22 | 16.179 | 0.000 | True | 65 9 9 9 9 9 9 9 |
| independent-i16-r0 | cs | beam-4 | 35.63 | 0.470 | 2.215 | True | 53 9 8 9 15 12 11 11 |
| independent-i16-r0 | cs | data-local | 25.86 | 0.015 | 1.409 | True | 49 11 9 12 12 13 10 12 |
| independent-i16-r0 | cs | stealing | 28.31 | 0.086 | 1.141 | True | 48 11 11 12 11 11 11 13 |
| independent-i1-r0 | disagg | fifo-hash | 93.52 | 0.004 | 10.737 | True | 20 14 16 15 14 18 11 20 |
| independent-i16-r0 | disagg | fifo-hash | 95.34 | 0.005 | 10.737 | True | 13 14 18 17 14 17 15 20 |

| case | cs best | s | disagg best | s | speedup cs/disagg |
|---|---|---|---|---|---|
| independent-i1-r0 | greedy-wait | 17.80 | fifo-hash | 93.52 | 5.25 |
| independent-i16-r0 | greedy-wait | 24.22 | fifo-hash | 95.34 | 3.94 |

| step | attempt | seconds |
|---|---|---|
| create | 1 | 20 |
| independent-i1-r0-cs | 1 | 538 |
| independent-i16-r0-cs | 1 | 401 |
| independent-i1-r0-disagg | 1 | 144 |
| independent-i16-r0-disagg | 1 | 138 |
| delete | 1 | 1 |

first start to last end: 20.7 min
