| run | mode | policy | iterations | tasks | wall s | policy s | payload over network GB | worker NIC rx GB | worker NIC tx GB | verified | outputs sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cs-droid | cs | beam-4 | DROID | 272 | 24.25 | 1.581 | 0.230 | 0.286 | 0.286 | True | 041002e05056 |
| cs-droid | cs | greedy | DROID | 272 | 77.16 | 62.357 | 0.911 | 0.964 | 0.964 | True | 041002e05056 |
| cs-i1 | cs | heft | 1 | 32 | 1.50 | 0.003 | 0.000 | 0.003 | 0.003 | True | 0f318e55a107 |
| cs-i1 | cs | data-local | 1 | 32 | 1.81 | 0.004 | 0.134 | 0.138 | 0.139 | True | 0f318e55a107 |
| cs-i1 | cs | beam-4 | 1 | 32 | 1.53 | 0.089 | 0.000 | 0.003 | 0.003 | True | 0f318e55a107 |
| cs-i128 | cs | heft | 128 | 32 | 19.88 | 0.004 | 0.000 | 0.044 | 0.044 | True | ac306eca697e |
| cs-i128 | cs | data-local | 128 | 32 | 19.09 | 0.005 | 0.336 | 0.376 | 0.382 | True | ac306eca697e |
| cs-i128 | cs | beam-4 | 128 | 32 | 21.80 | 0.189 | 0.000 | 0.046 | 0.046 | True | ac306eca697e |
| cs-i2048 | cs | beam-4 | 2048 | 32 | 272.71 | 0.232 | 0.000 | 0.558 | 0.558 | True | 648be59703b4 |
| disagg-droid | disagg | fifo-hash | DROID | 272 | 63.42 | 0.010 | 17.415 | 11.401 | 6.458 | True | 041002e05056 |
| disagg-i1 | disagg | heft | 1 | 32 | 10.93 | 0.004 | 2.684 | 2.161 | 0.613 | True | 0f318e55a107 |
| disagg-i1 | disagg | fifo-hash | 1 | 32 | 4.94 | 0.001 | 2.684 | 2.158 | 0.551 | True | 0f318e55a107 |
| disagg-i128 | disagg | heft | 128 | 32 | 20.55 | 0.004 | 2.684 | 2.168 | 0.576 | True | ac306eca697e |
| disagg-i128 | disagg | fifo-hash | 128 | 32 | 17.54 | 0.002 | 2.684 | 2.169 | 0.568 | True | ac306eca697e |
