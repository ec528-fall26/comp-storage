| case | mode | policy | wall s | policy s | network payload GB | verified | tasks per worker |
|---|---|---|---|---|---|---|---|
| wave00 | cs | beam-4 | 44.79 | 3.185 | 0.524 | True | 60 55 66 57 53 58 62 61 |
| wave00 | cs | heft | 54.79 | 0.100 | 4.961 | True | 56 63 64 58 70 58 46 57 |
| wave00 | cs | data-local | 39.06 | 0.069 | 0.556 | True | 60 55 54 58 59 53 66 67 |
| wave00 | disagg | heft | 158.85 | 0.062 | 30.321 | True | 54 70 53 55 50 68 53 69 |
| wave00 | disagg | fifo-hash | 179.54 | 0.017 | 30.321 | True | 56 57 57 69 62 54 56 61 |

| case | cs best | s | disagg best | s | speedup cs/disagg |
|---|---|---|---|---|---|
| wave00 | data-local | 39.06 | heft | 158.85 | 4.07 |

| step | attempt | seconds |
|---|---|---|
| wave00-ingest | 1 | 27 |
| wave00-cs | 1 | 539 |
| wave00-disagg | 1 | 479 |
| wave00-delete | 1 | 1 |

first start to last end: 17.4 min
