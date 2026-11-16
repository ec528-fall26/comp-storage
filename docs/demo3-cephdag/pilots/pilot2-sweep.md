| case | mode | policy | wall s | policy s | network payload GB | verified | tasks per worker |
|---|---|---|---|---|---|---|---|
| independent-i1-r0 | cs | data-local | 4.23 | 0.008 | 0.201 | True | 9 9 8 8 8 7 8 7 |
| independent-i1-r0 | cs | heft | 2.59 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i4-r0 | cs | data-local | 4.01 | 0.008 | 0.134 | True | 7 8 8 7 9 8 8 9 |
| independent-i4-r0 | cs | heft | 2.70 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i16-r0 | cs | data-local | 4.32 | 0.008 | 0.067 | True | 8 8 9 8 8 7 8 8 |
| independent-i16-r0 | cs | heft | 3.03 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i64-r0 | cs | data-local | 5.94 | 0.009 | 0.067 | True | 8 9 8 8 8 8 8 7 |
| independent-i64-r0 | cs | heft | 4.52 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i256-r0 | cs | data-local | 10.93 | 0.008 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i256-r0 | cs | heft | 10.96 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i1024-r0 | cs | heft | 35.69 | 0.006 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i1024-r0 | cs | data-local | 35.58 | 0.008 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i2048-r0 | cs | data-local | 69.23 | 0.008 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i2048-r0 | cs | heft | 68.40 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i4096-r0 | cs | data-local | 134.63 | 0.009 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i4096-r0 | cs | heft | 134.36 | 0.007 | 0.000 | True | 8 8 8 8 8 8 8 8 |
| independent-i1-r0 | disagg | heft | 17.90 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i1-r0 | disagg | fifo-hash | 18.86 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i4-r0 | disagg | heft | 18.43 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i4-r0 | disagg | fifo-hash | 19.07 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i16-r0 | disagg | heft | 18.82 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i16-r0 | disagg | fifo-hash | 19.26 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i64-r0 | disagg | heft | 20.48 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i64-r0 | disagg | fifo-hash | 21.18 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i256-r0 | disagg | heft | 27.11 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i256-r0 | disagg | fifo-hash | 27.05 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i1024-r0 | disagg | fifo-hash | 52.11 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i1024-r0 | disagg | heft | 52.71 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i2048-r0 | disagg | heft | 84.58 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i2048-r0 | disagg | fifo-hash | 85.29 | 0.002 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i4096-r0 | disagg | heft | 150.49 | 0.005 | 5.369 | True | 8 8 8 8 8 8 8 8 |
| independent-i4096-r0 | disagg | fifo-hash | 150.02 | 0.003 | 5.369 | True | 8 8 8 8 8 8 8 8 |

| case | cs best | s | disagg best | s | speedup cs/disagg |
|---|---|---|---|---|---|
| independent-i1-r0 | heft | 2.59 | heft | 17.90 | 6.90 |
| independent-i4-r0 | heft | 2.70 | heft | 18.43 | 6.82 |
| independent-i16-r0 | heft | 3.03 | heft | 18.82 | 6.22 |
| independent-i64-r0 | heft | 4.52 | heft | 20.48 | 4.53 |
| independent-i256-r0 | data-local | 10.93 | fifo-hash | 27.05 | 2.47 |
| independent-i1024-r0 | data-local | 35.58 | fifo-hash | 52.11 | 1.46 |
| independent-i2048-r0 | heft | 68.40 | heft | 84.58 | 1.24 |
| independent-i4096-r0 | heft | 134.36 | fifo-hash | 150.02 | 1.12 |

| step | attempt | seconds |
|---|---|---|
| create | 1 | 12 |
| independent-i1-r0-cs | 1 | 227 |
| independent-i4-r0-cs | 1 | 53 |
| independent-i16-r0-cs | 1 | 53 |
| independent-i64-r0-cs | 1 | 56 |
| independent-i256-r0-cs | 1 | 67 |
| independent-i1024-r0-cs | 1 | 117 |
| independent-i2048-r0-cs | 1 | 183 |
| independent-i4096-r0-cs | 1 | 315 |
| independent-i1-r0-disagg | 1 | 88 |
| independent-i4-r0-disagg | 1 | 82 |
| independent-i16-r0-disagg | 1 | 83 |
| independent-i64-r0-disagg | 1 | 87 |
| independent-i256-r0-disagg | 1 | 99 |
| independent-i1024-r0-disagg | 1 | 150 |
| independent-i2048-r0-disagg | 1 | 215 |
| independent-i4096-r0-disagg | 1 | 346 |
| delete | 1 | 1 |

first start to last end: 37.2 min
