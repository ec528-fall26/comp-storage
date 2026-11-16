### Wall time against lower bounds

| case | mode | policy | wall s | busiest link (GB) | link s | node s | bound s | wall/bound | single-slot serial s | wall/serial |
|---|---|---|---|---|---|---|---|---|---|---|
| sweep i1 | cs | heft | 2.8 | none (0.00) | 0.0 | 2.9 | 2.9 | 0.97 | 2.9 | 0.97 |
| sweep i1 | cs | beam-4 | 3.0 | none (0.00) | 0.0 | 2.9 | 2.9 | 1.03 | 2.9 | 1.03 |
| sweep i1 | cs | data-local | 4.0 | 1-tx (0.07) | 1.4 | 3.0 | 3.0 | 1.36 | 4.4 | 0.92 |
| sweep i1 | disagg | heft | 18.1 | 0-tx (0.54) | 11.4 | 0.2 | 11.4 | 1.59 | 14.4 | 1.25 |
| sweep i1 | disagg | fifo-hash | 19.1 | 0-tx (0.54) | 11.4 | 0.2 | 11.4 | 1.67 | 14.5 | 1.32 |
| sweep i128 | cs | heft | 35.8 | none (0.00) | 0.0 | 35.8 | 35.8 | 1.00 | 35.8 | 1.00 |
| sweep i128 | cs | data-local | 40.6 | 1-tx (0.20) | 4.3 | 39.3 | 39.3 | 1.03 | 40.0 | 1.02 |
| sweep i128 | cs | beam-4 | 40.7 | 7-tx (0.13) | 2.9 | 38.9 | 38.9 | 1.04 | 38.9 | 1.04 |
| sweep i128 | disagg | fifo-hash | 38.2 | 9-rx (0.60) | 12.8 | 22.4 | 22.4 | 1.71 | 35.1 | 1.09 |
| sweep i128 | disagg | heft | 43.6 | 0-tx (0.54) | 11.4 | 26.4 | 26.4 | 1.65 | 40.7 | 1.07 |
| droid | cs | data-local | 39.1 | 2-tx (0.13) | 2.8 | 38.3 | 38.3 | 1.02 | 40.0 | 0.98 |
| droid | cs | beam-4 | 44.8 | 2-tx (0.18) | 3.7 | 40.9 | 40.9 | 1.10 | 41.5 | 1.08 |
| droid | cs | heft | 54.8 | 3-tx (0.82) | 17.3 | 36.1 | 36.1 | 1.52 | 47.4 | 1.16 |
| droid | disagg | heft | 158.9 | 5-tx (2.83) | 60.0 | 23.7 | 60.0 | 2.65 | 104.2 | 1.52 |
| droid | disagg | fifo-hash | 179.5 | 5-tx (2.83) | 60.0 | 26.4 | 60.0 | 2.99 | 113.9 | 1.58 |
| skew i1 | cs | greedy-wait | 17.8 | none (0.00) | 0.0 | 20.5 | 20.5 | 0.87 | 20.5 | 0.87 |
| skew i1 | cs | stealing | 18.5 | 0-tx (0.74) | 15.7 | 17.4 | 17.4 | 1.07 | 17.4 | 1.07 |
| skew i1 | cs | data-local | 18.8 | 0-tx (0.81) | 17.1 | 16.9 | 17.1 | 1.10 | 16.9 | 1.11 |
| skew i1 | cs | greedy | 23.1 | 0-tx (0.94) | 20.0 | 16.1 | 20.0 | 1.16 | 16.1 | 1.44 |
| skew i1 | cs | beam-4 | 29.2 | 0-tx (1.28) | 27.1 | 15.0 | 27.1 | 1.08 | 25.0 | 1.17 |
| skew i1 | cs | heft | 44.7 | 0-tx (2.08) | 44.2 | 10.7 | 44.2 | 1.01 | 11.2 | 3.98 |
| skew i1 | disagg | fifo-hash | 93.5 | 0-tx (4.36) | 92.6 | 0.4 | 92.6 | 1.01 | 36.0 | 2.60 |

### Where each worker's time goes (mean over workers, % of wall)

| case | mode | policy | read | compute | write | dispatch (slack+gap+head+publish) | idle at end | read: remote bytes at line rate | read: rest (local, sharing, per-read cost) | write: remote bytes at line rate | write: rest |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sweep i1 | cs | heft | 49 | 14 | 15 | 11.3 | 9.8 | 0 | 49 | 0 | 15 |
| sweep i1 | cs | beam-4 | 44 | 15 | 16 | 18.3 | 7.2 | 0 | 44 | 0 | 16 |
| sweep i1 | cs | data-local | 47 | 10 | 11 | 8.2 | 23.7 | 13 | 33 | 0 | 11 |
| sweep i1 | disagg | heft | 70 | 1 | 22 | 4.7 | 2.0 | 63 | 7 | 16 | 7 |
| sweep i1 | disagg | fifo-hash | 66 | 1 | 22 | 7.9 | 2.8 | 60 | 7 | 15 | 7 |
| sweep i128 | cs | heft | 5 | 75 | 1 | 0.7 | 18.5 | 0 | 5 | 0 | 1 |
| sweep i128 | cs | data-local | 7 | 87 | 1 | 0.7 | 4.5 | 2 | 5 | 0 | 1 |
| sweep i128 | cs | beam-4 | 7 | 80 | 1 | 6.1 | 5.4 | 2 | 6 | 0 | 1 |
| sweep i128 | disagg | fifo-hash | 34 | 52 | 9 | 1.0 | 3.8 | 30 | 4 | 7 | 2 |
| sweep i128 | disagg | heft | 29 | 50 | 8 | 1.3 | 11.4 | 26 | 3 | 7 | 2 |
| droid | cs | data-local | 20 | 64 | 8 | 4.9 | 3.6 | 4 | 16 | 0 | 8 |
| droid | cs | beam-4 | 17 | 56 | 6 | 12.1 | 8.9 | 3 | 14 | 0 | 6 |
| droid | cs | heft | 40 | 44 | 5 | 5.3 | 4.7 | 24 | 16 | 0 | 5 |
| droid | disagg | heft | 55 | 15 | 23 | 3.0 | 4.7 | 33 | 22 | 18 | 5 |
| droid | disagg | fifo-hash | 62 | 13 | 20 | 2.8 | 1.9 | 29 | 33 | 16 | 4 |
| skew i1 | cs | greedy-wait | 11 | 2 | 3 | 60.3 | 23.6 | 0 | 11 | 0 | 3 |
| skew i1 | cs | stealing | 75 | 3 | 4 | 7.2 | 10.0 | 11 | 65 | 0 | 4 |
| skew i1 | cs | data-local | 74 | 2 | 3 | 8.0 | 12.3 | 16 | 58 | 0 | 3 |
| skew i1 | cs | greedy | 68 | 1 | 3 | 15.6 | 12.8 | 30 | 38 | 0 | 3 |
| skew i1 | cs | beam-4 | 82 | 1 | 2 | 9.1 | 5.5 | 20 | 62 | 0 | 2 |
| skew i1 | cs | heft | 77 | 1 | 1 | 4.0 | 17.2 | 16 | 61 | 0 | 1 |
| skew i1 | disagg | fifo-hash | 87 | 0 | 7 | 3.0 | 2.2 | 24 | 63 | 6 | 1 |

### Storage-node links (mean over the 8 storage nodes)

| case | mode | policy | tx active % of wall | tx with ≥2 flows % | tx mean util % | rx active % | rx with ≥2 flows % | reads alone MB/s (n) | reads shared MB/s (n) | writes alone MB/s (n) | writes shared MB/s (n) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sweep i1 | cs | heft | 0 | 0 | 0 | 0 | 0 | – | – | – | – |
| sweep i1 | cs | beam-4 | 0 | 0 | 0 | 0 | 0 | – | – | – | – |
| sweep i1 | cs | data-local | 39 | 0 | 35 | 39 | 0 | 44 (3) | – | – | – |
| sweep i1 | disagg | heft | 70 | 0 | 63 | 17 | 4 | 43 (64) | – | 43 (42) | 23 (22) |
| sweep i1 | disagg | fifo-hash | 66 | 0 | 60 | 16 | 5 | 43 (64) | – | 43 (38) | 25 (26) |
| sweep i128 | cs | heft | 0 | 0 | 0 | 0 | 0 | – | – | – | – |
| sweep i128 | cs | data-local | 7 | 0 | 6 | 5 | 0 | 41 (5) | – | – | – |
| sweep i128 | cs | beam-4 | 5 | 2 | 5 | 5 | 0 | 44 (2) | 24 (2) | – | – |
| sweep i128 | disagg | fifo-hash | 34 | 0 | 30 | 8 | 1 | 42 (64) | – | 42 (55) | 28 (9) |
| sweep i128 | disagg | heft | 29 | 0 | 26 | 7 | 1 | 42 (64) | – | 41 (54) | 26 (10) |
| droid | cs | data-local | 4 | 0 | 4 | 4 | 0 | 44 (10) | – | – | – |
| droid | cs | beam-4 | 3 | 1 | 3 | 4 | 0 | 43 (13) | 28 (2) | – | – |
| droid | cs | heft | 26 | 5 | 24 | 33 | 0 | 42 (85) | 21 (23) | – | – |
| droid | disagg | heft | 35 | 13 | 33 | 19 | 4 | 43 (211) | 23 (143) | 44 (191) | 30 (45) |
| droid | disagg | fifo-hash | 31 | 20 | 29 | 17 | 3 | 43 (123) | 21 (231) | 44 (188) | 29 (48) |
| skew i1 | cs | greedy-wait | 0 | 0 | 0 | 0 | 0 | – | – | – | – |
| skew i1 | cs | stealing | 85 | 82 | 85 | 73 | 0 | – | 10 (11) | – | – |
| skew i1 | cs | data-local | 27 | 19 | 26 | 74 | 0 | 43 (3) | 12 (14) | – | – |
| skew i1 | cs | greedy | 33 | 8 | 30 | 70 | 0 | 42 (27) | 11 (12) | – | – |
| skew i1 | cs | beam-4 | 25 | 13 | 23 | 76 | 0 | 42 (14) | 9 (19) | – | – |
| skew i1 | cs | heft | 22 | 16 | 21 | 84 | 0 | 43 (10) | 9 (30) | – | – |
| skew i1 | disagg | fifo-hash | 26 | 12 | 24 | 7 | 0 | 43 (63) | 8 (65) | 43 (114) | 30 (14) |

### Dispatch overhead per task (seconds)

| case | mode | policy | slack p50 / p90 | gap p50 / p90 / max | coordinator decision interval p50 / p90 | completion record write p50 / p90 |
|---|---|---|---|---|---|---|
| sweep i1 | cs | heft | 0.020 / 0.027 | 0.002 / 0.014 / 0.04 | 0.015 / 0.027 | 0.010 / 0.012 |
| sweep i1 | cs | beam-4 | 0.021 / 0.031 | 0.006 / 0.080 / 0.33 | 0.016 / 0.030 | 0.009 / 0.012 |
| sweep i1 | cs | data-local | 0.020 / 0.028 | 0.002 / 0.018 / 0.08 | 0.015 / 0.029 | 0.007 / 0.013 |
| sweep i1 | disagg | heft | 0.039 / 0.080 | 0.020 / 0.052 / 0.24 | 0.038 / 0.166 | 0.009 / 0.018 |
| sweep i1 | disagg | fifo-hash | 0.066 / 0.199 | 0.039 / 0.252 / 0.49 | 0.048 / 0.257 | 0.011 / 0.023 |
| sweep i128 | cs | heft | 0.015 / 0.020 | 0.002 / 0.002 / 0.01 | 0.035 / 0.280 | 0.006 / 0.010 |
| sweep i128 | cs | data-local | 0.016 / 0.024 | 0.002 / 0.003 / 0.02 | 0.042 / 0.349 | 0.006 / 0.007 |
| sweep i128 | cs | beam-4 | 0.017 / 0.021 | 0.004 / 1.387 / 2.60 | 0.042 / 0.345 | 0.006 / 0.007 |
| sweep i128 | disagg | fifo-hash | 0.022 / 0.043 | 0.002 / 0.019 / 0.03 | 0.083 / 0.344 | 0.006 / 0.012 |
| sweep i128 | disagg | heft | 0.028 / 0.065 | 0.002 / 0.036 / 0.16 | 0.080 / 0.420 | 0.006 / 0.013 |
| droid | cs | data-local | 0.016 / 0.023 | 0.002 / 0.006 / 0.65 | 0.013 / 0.053 | 0.006 / 0.010 |
| droid | cs | beam-4 | 0.017 / 0.024 | 0.005 / 0.192 / 1.56 | 0.015 / 0.057 | 0.006 / 0.010 |
| droid | cs | heft | 0.023 / 0.044 | 0.002 / 0.019 / 1.07 | 0.024 / 0.094 | 0.006 / 0.010 |
| droid | disagg | heft | 0.029 / 0.064 | 0.002 / 0.038 / 6.99 | 0.041 / 0.279 | 0.007 / 0.014 |
| droid | disagg | fifo-hash | 0.032 / 0.081 | 0.002 / 0.056 / 2.89 | 0.049 / 0.314 | 0.006 / 0.016 |
| skew i1 | cs | greedy-wait | 0.051 / 0.097 | 0.176 / 1.639 / 1.70 | 0.074 / 0.101 | 0.006 / 0.006 |
| skew i1 | cs | stealing | 0.037 / 0.129 | 0.005 / 0.040 / 0.61 | 0.050 / 0.140 | 0.006 / 0.012 |
| skew i1 | cs | data-local | 0.052 / 0.117 | 0.007 / 0.072 / 0.25 | 0.052 / 0.143 | 0.006 / 0.010 |
| skew i1 | cs | greedy | 0.049 / 0.094 | 0.059 / 0.407 / 1.04 | 0.079 / 0.148 | 0.006 / 0.010 |
| skew i1 | cs | beam-4 | 0.051 / 0.101 | 0.015 / 0.411 / 0.83 | 0.054 / 0.177 | 0.006 / 0.010 |
| skew i1 | cs | heft | 0.072 / 0.115 | 0.032 / 0.066 / 0.10 | 0.077 / 0.212 | 0.006 / 0.006 |
| skew i1 | disagg | fifo-hash | 0.092 / 0.150 | 0.059 / 0.086 / 0.30 | 0.125 / 0.543 | 0.016 / 0.023 |

### Speedup now and with read-ahead (best policy per mode, seconds)

| case | cs now | disagg now | speedup now | cs read-ahead, gaps hidden / kept | disagg read-ahead, gaps hidden / kept | disagg, contention-adjusted (×compute) | disagg floor | speedup, read-ahead in disagg only | speedup, read-ahead in both (hidden / kept) | speedup, both, adjusted | disagg floor / cs read-ahead |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sweep i1 | 2.8 (heft) | 18.1 (heft) | 6.47 | 1.6 / 1.9 | 13.6 / 14.3 | 13.6 (×2.23) | 11.4 | 4.85 | 8.52 / 7.45 | 8.54 | 7.17 |
| sweep i2 | 2.9 (heft) | 18.2 (heft) | 6.22 | 1.7 / 1.9 | 13.6 / 13.8 | 13.7 (×2.14) | 11.4 | 4.65 | 8.17 / 7.40 | 8.20 | 6.85 |
| sweep i4 | 3.6 (beam-4) | 18.8 (heft) | 5.28 | 2.0 / 2.2 | 13.7 / 14.5 | 13.8 (×2.03) | 11.4 | 3.84 | 6.89 / 6.46 | 6.94 | 5.75 |
| sweep i8 | 4.8 (heft) | 18.4 (heft) | 3.86 | 3.1 / 3.3 | 13.6 / 13.9 | 13.6 (×2.39) | 11.4 | 2.85 | 4.35 / 4.19 | 4.38 | 3.66 |
| sweep i16 | 7.1 (heft) | 20.8 (fifo-hash) | 2.93 | 4.9 / 5.1 | 13.9 / 15.0 | 14.0 (×1.65) | 11.4 | 1.96 | 2.82 / 2.91 | 2.84 | 2.31 |
| sweep i32 | 11.9 (heft) | 21.3 (heft) | 1.78 | 9.5 / 9.7 | 13.9 / 14.5 | 14.3 (×2.28) | 11.4 | 1.16 | 1.45 / 1.49 | 1.50 | 1.19 |
| sweep i64 | 18.5 (heft) | 28.1 (fifo-hash) | 1.51 | 16.7 / 16.8 | 16.4 / 16.9 | 23.2 (×1.58) | 12.6 | 0.89 | 0.99 / 1.00 | 1.39 | 0.75 |
| sweep i128 | 35.8 (heft) | 38.2 (fifo-hash) | 1.07 | 34.1 / 34.3 | 26.9 / 27.0 | 38.3 (×1.51) | 22.4 | 0.75 | 0.79 / 0.79 | 1.12 | 0.65 |
| droid | 39.1 (data-local) | 158.9 (heft) | 4.07 | 29.4 / 30.9 | 95.9 / 98.4 | 95.9 (×1.06) | 60.0 | 2.45 | 3.26 / 3.18 | 3.26 | 2.04 |
| skew i1 | 17.8 (greedy-wait) | 93.5 (fifo-hash) | 5.25 | 7.8 / 14.2 | 92.6 / 92.6 | 92.6 (×1.32) | 92.6 | 5.20 | 11.88 / 6.51 | 11.88 | 11.88 |
| skew i16 | 24.2 (greedy-wait) | 95.3 (fifo-hash) | 3.94 | 11.5 / 17.2 | 92.6 / 92.6 | 92.6 (×1.71) | 92.6 | 3.82 | 8.09 / 5.40 | 8.09 | 8.09 |
