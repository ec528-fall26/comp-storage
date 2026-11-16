| run | mode | policy | iterations | tasks | wall s | policy s | payload over network GB | worker NIC rx GB | worker NIC tx GB | verified | outputs sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cs-droid | cs | beam-4 | DROID | 272 | 31.12 | 1.510 | 0.358 | 0.425 | 0.425 | True | 041002e05056 |
| cs-droid | cs | greedy | DROID | 272 | 72.56 | 60.081 | 0.812 | 0.845 | 0.845 | True | 041002e05056 |
| cs-i1 | cs | heft | 1 | 32 | 1.60 | 0.003 | 0.000 | 0.004 | 0.004 | True | 0f318e55a107 |
| cs-i1 | cs | data-local | 1 | 32 | 1.53 | 0.004 | 0.000 | 0.004 | 0.004 | True | 0f318e55a107 |
| cs-i1 | cs | beam-4 | 1 | 32 | 1.57 | 0.104 | 0.000 | 0.003 | 0.003 | True | 0f318e55a107 |
| cs-i128 | cs | heft | 128 | 32 | 20.32 | 0.004 | 0.000 | 0.044 | 0.044 | True | ac306eca697e |
| cs-i128 | cs | data-local | 128 | 32 | 20.58 | 0.005 | 0.201 | 0.243 | 0.243 | True | ac306eca697e |
| cs-i128 | cs | beam-4 | 128 | 32 | 19.86 | 0.227 | 0.000 | 0.044 | 0.044 | True | ac306eca697e |
| cs-i2048 | cs | beam-4 | 2048 | 32 | 254.79 | 0.211 | 0.067 | 0.617 | 0.617 | True | 648be59703b4 |
| disagg-droid | disagg | fifo-hash | DROID | 272 | 103.80 | 0.009 | 17.415 | 11.382 | 6.212 | True | 041002e05056 |
| disagg-i1 | disagg | heft | 1 | 32 | 16.28 | 0.003 | 2.684 | 2.154 | 0.545 | True | 0f318e55a107 |
| disagg-i1 | disagg | fifo-hash | 1 | 32 | 9.52 | 0.001 | 2.684 | 2.155 | 0.547 | True | 0f318e55a107 |
| disagg-i128 | disagg | heft | 128 | 32 | 21.35 | 0.004 | 2.684 | 2.160 | 0.555 | True | ac306eca697e |
| disagg-i128 | disagg | fifo-hash | 128 | 32 | 22.69 | 0.002 | 2.684 | 2.166 | 0.564 | True | ac306eca697e |
