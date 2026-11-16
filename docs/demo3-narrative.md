# Demo 3 — ten-minute narrative

For Aakash's 18 main slides in [demo-3-aakash.pptx](../slides/demo-3-aakash.pptx). Slides 19–22 are for questions. This allocates ten minutes to Aakash's section, following the team's keyword-slice demonstration. The timings include pointing at figures and changing slides.

## Through-line

Computing beside the data can remove a large transfer cost. Across multiple storage nodes, the scheduler must balance locality against parallelism and shared-link contention. Its decisions must also save enough execution time to repay the time spent planning.

## Timing

| Slide | Topic | Time | Elapsed |
| --- | --- | --- | --- |
| 1 | Computational storage | 15 s | 0:15 |
| 2 | DROID and robot learning | 20 s | 0:35 |
| 3 | Inside one demonstration | 20 s | 0:55 |
| 4 | The preprocessing pipeline | 40 s | 1:35 |
| 5 | Separate and combined | 25 s | 2:00 |
| 6 | Where a task's time goes | 40 s | 2:40 |
| 7 | Data is uneven | 30 s | 3:10 |
| 8 | A plan that ignores the shared link | 40 s | 3:50 |
| 9 | Client interface | 30 s | 4:20 |
| 10 | Scheduler inputs and decisions | 40 s | 5:00 |
| 11 | Combined testbed | 25 s | 5:25 |
| 12 | Separate testbed | 30 s | 5:55 |
| 13 | Arithmetic intensity | 40 s | 6:35 |
| 14 | Network speed | 35 s | 7:10 |
| 15 | DROID at 496 GB | 45 s | 7:55 |
| 16 | Scheduling uneven data | 45 s | 8:40 |
| 17 | Planning overhead on DROID | 55 s | 9:35 |
| 18 | Findings | 25 s | 10:00 |

## What to say

### 1. Computational storage · 0:00–0:15

“The keyword-slice demo showed how much traffic we can avoid by processing data where it is stored. My part asks when that also saves time, and how to schedule the work across multiple storage nodes.”

### 2. DROID and robot learning · 0:15–0:35

“DROID gives us a concrete workload. It contains 76,000 robot demonstrations, with terabytes of raw observations. To use those demonstrations for learning, we first have to read and transform them. At that scale, the placement of the preprocessing work can matter.”

### 3. Inside one demonstration · 0:35–0:55

“One demonstration combines three camera views, a language instruction, and the robot's actions and state over time. Preparing it means processing several synchronized streams. The images account for much of the data we need to move through the pipeline.”

### 4. The preprocessing pipeline · 0:55–1:35

“Here is the pipeline we implemented. We decode and resize the images, encode video, compute frame statistics, and write the robot state as a table.

“Follow the frames in the middle. A typical partition starts with about 31 megabytes of episode data and produces about 68 megabytes of resized frames. We store those frames, and two downstream tasks read them. So the amount of data moved can be several times the size of the original input. That makes this a useful workload for studying where computation runs.”

*Point along the two arrows leaving the frames object.*

### 5. Separate and combined · 1:35–2:00

“With separate storage and compute, the input crosses the network to reach a worker, and the output crosses back to storage. Computational storage puts workers beside the stored data. When the inputs and outputs stay on that node, those transfers become local reads and writes. The benefit depends on how expensive the transfers were.”

### 6. Where a task's time goes · 2:00–2:40

“These are measured read, compute, and write times for a synthetic task. With one iteration of computation, the separate worker spends about 2.3 seconds reading, two hundredths of a second computing, and half a second writing. Almost all its time is in I/O.

“The local task takes about four tenths of a second. With 256 iterations, computation occupies a larger share of both bars. This gives us our first prediction. As computation per byte increases, removing the network should produce a smaller relative speedup.”

*Trace the read and write segments, then the larger compute segment below.*

### 7. Data is uneven · 2:40–3:10

“There is a second problem once we have multiple storage nodes. Here, node zero holds 65 of the 128 shards, while each other node holds nine. Keeping all the work local concentrates it on node zero and can leave other workers idle. Moving some work could help, but every shard leaving node zero shares that node's single outgoing link.”

### 8. A plan that ignores the shared link · 3:10–3:50

“Our original HEFT scheduler estimates transfer times independently. On this workload it plans to move 31 tasks off node zero and predicts a finish in 11.5 seconds.

“Each shard needs about 1.43 seconds of that link's capacity. Thirty-one shards therefore require about 44 seconds of link time. The measured run takes 45.5 seconds. The plan has enough workers, but it has overcommitted the network. A useful placement decision has to account for both transfer size and contention for the link.”

*Pause on the 11.5-to-45.5 discrepancy before explaining the multiplication.*

### 9. Client interface · 3:50–4:20

“This is how a client submits work to our multi-node system. It opens a stored dataset, constructs a task graph, and selects a scheduling policy. This example maps a function over the partitions and then reduces the outputs. The task descriptions include compute parameters and expected output sizes. We compare schedulers by changing the policy while keeping the workload fixed.”

### 10. Scheduler inputs and decisions · 4:20–5:00

“The coordinator tracks the graph, completed outputs, and running tasks. A policy combines that state with calibrated disk, network, and compute rates to decide what runs next.

“A decision identifies a task, a worker, and a storage node for each output. Those choices can differ. A worker may compute an intermediate that is best stored near its next consumer. The policy can also wait when dispatching immediately would be costly. The coordinator executes those decisions, and the workers read, compute, and write through Ceph.”

### 11. Combined testbed · 5:00–5:25

“We built an eight-storage-node lab using real Ceph inside Docker containers on one physical host. Each storage node has its own OSD and pool, which gives us explicit data placement. The default links are shaped to 400 megabits per second. In the combined configuration, workers run on the storage nodes.”

### 12. Separate testbed · 5:25–5:55

“For the separate configuration, workers run on eight compute-only nodes and access the same storage cluster. The intensity and network sweeps use two workers per active node, sixteen on each side. The large DROID run uses eight on each side.

“All these containers share the host's physical resources. This is a controlled emulated topology. We also verify output digests across the configurations to check that they perform equivalent work.”

### 13. Arithmetic intensity · 5:55–6:35

“The first result follows the prediction from the task-time bars. With sixteen workers on each side, the plotted speedup falls from 5.6 times for light computation to 1.08 times for heavy computation.

“These points use HEFT on the combined side and FIFO with hashed output placement on the separate side. As the task does more computation on the same input, the transfer cost becomes a smaller fraction of its total runtime. Computational storage stays faster throughout this measured, worker-matched sweep.”

*Follow the curve down to the one-times reference line.*

### 14. Network speed · 6:35–7:10

“Changing the network gives us another way to test that explanation. For the light workload, the speedup falls from about 5.7 times at 400 megabits to 1.3 times at 2.5 gigabits per second. At that point, network throughput approaches the measured local read rate.

“This is consistent with the team's AWS result, where avoiding traffic gave little runtime improvement. The transfer savings become most valuable when the network constrains how quickly the workers can process data.”

### 15. DROID at 496 GB · 7:10–7:55

“For the real pipeline, we processed 496 gigabytes in seven batches, with about 64,000 tasks. The total runtime fell from 5.85 hours to 1.55 hours, a 3.76-times speedup. Network traffic fell from about 4.1 terabytes to 6.2 gigabytes.

“The large traffic reduction comes partly from the intermediate frames we saw earlier. The separate workers write them and then read them twice across the network. This result is for that stored-intermediate pipeline. In a smaller worker-matched test with those stages fused, the speedup was 1.79 times.”

### 16. Scheduling uneven data · 7:55–8:40

“Now return to the overloaded storage node. The dashed line is the data-local policy. Lower is better. Original HEFT is slower across these cases. Our link-aware version reserves sending and receiving capacity when it plans each transfer. It improves on data-local by about five to twenty-one percent, and cuts the light-workload example from 45.5 to 18.3 seconds.

“We also tested a linear program that decides the work split once at job start. With an improved dispatcher, it beats link-aware HEFT on these synthetic skewed workloads. That leads to the final question. Does the extra planning pay on DROID?”

### 17. Planning overhead on DROID · 8:40–9:35

“Here we use fused DROID with uneven placement. The pale part of each bar is time spent planning, during which workers wait for decisions. Data-local finishes fastest overall.

“The linear program does find the fastest schedule to execute in both batches. But it takes seventeen to twenty-three seconds to construct that schedule, which exceeds the execution time it saves. DROID partitions have different sizes, so this implementation creates a much larger optimization problem than it does for uniform synthetic tasks.

“That identifies a concrete next step. Group similarly sized partitions to reduce the optimization problem, then measure whether the cheaper planner improves total wall time. Planning cost has to count in the result.”

### 18. Findings · 9:35–10:00

“We now have results for the three challenges from Demo 1. Local processing made our 496-gigabyte DROID pipeline 3.76 times faster. Its benefit shrinks as computation grows or the network gets faster. With uneven data, the scheduler needs to account for shared-link contention. Our next step is making the improved planning cheap enough to help the real pipeline.”

## Backup slides and likely questions

| Question | Where to go | Answer |
| --- | --- | --- |
| How does a task reach a worker? | Slide 19 | The coordinator calls the policy, dispatches a task, and records its completion and output locations. Workers access the assigned Ceph pools. |
| How much better was the linear program? | Slide 20 | `lp-plan2` improved on `heft-cc` by roughly 6–28% across the four synthetic skew intensities, using comparisons within the corresponding campaigns. At the lightest intensity it took 12.8 seconds. |
| Did deeper search help? | Slide 21 | Native MCTS-128 was 49% and 44% slower than link-aware HEFT in the light and medium cases. The high-intensity difference was within observed run drift. Gumbel MCTS was evaluated in simulation. |
| Were the worker counts fair? | Slide 22 | The main intensity plot compares sixteen workers per side. An earlier eight-versus-sixteen comparison mixed placement with worker-count effects. |
| How large was the cluster? | Slides 11–12 | Eight storage containers, eight compute-only containers, and one router on one physical host. Each pool has one replica. |
| Did half a terabyte fit at once? | Slide 15 | The 496 GB of unique input was processed in seven batches of roughly 65–75 GB, with one run per configuration per batch. |
| Why does half a terabyte generate four terabytes of traffic? | Slide 4 | Resized frames are larger than the encoded input and are stored once and read by two downstream stages. Other inputs and outputs add further transfers. |
| What happens when stages are fused? | Slide 15 | On three shards with sixteen workers per side, the measured speedup was 1.79× for fused processing and 4.30× for the stored-intermediate pipeline. |
| Does the team's HTTP API already use this scheduler? | Slide 9 | The API demo uses the CephFS job system. This multi-node experiment uses its own graph client and Ceph RADOS execution system. Connecting the API to this scheduler remains future work. |

## Delivery notes

- Reach the client interface by 3:50 and the first results plot by 5:55. If behind, shorten the code walkthrough to the graph construction and policy choice.
- The 45.5-second HEFT result is the clearest scheduling example. Give the audience time to understand why 31 transfers share one link.
- Introduce the linear program in one sentence on slide 16 so the `lp-plan2` bar on slide 17 has context. Use slide 20 for the detailed improvement.
- Describe the lab as one physical host and give the worker counts. The existing draft's claim that each worker has identical CPU resources overstates the setup, whose storage and compute-only container budgets differ.
- The source draft allocates 500 seconds across fourteen generated main slides and leaves four reused slides untimed. This script allocates the full ten minutes across all eighteen main slides.

## Evidence

The timings and wording above follow the current deck's actual slide order. The result values come from the supplied records and their derived tables.

- [Task stages](demo3-cephdag/demo-data/t8_task_stages.csv), [HEFT plan](demo3-cephdag/demo-data/t11_heft_plan.csv), and [DROID object sizes](demo3-cephdag/demo-data/t10_droid_objects.csv)
- [Worker-matched intensity sweep](demo3-cephdag/demo-data/t1_slots.csv) and [network sweep](demo3-cephdag/demo-data/t6_netsweep.csv)
- [DROID totals](demo3-cephdag/demo-data/t2_droid_totals.csv) and [fused-pipeline comparison](demo3-cephdag/demo-data/t2_droid_fused.csv)
- [Skewed scheduling results](demo3-cephdag/demo-data/t4_sched_skew_cells.csv), [LP results](demo3-cephdag/RESULTS-lp-plan2.md), and [DROID planning times](demo3-cephdag/demo-data/t7_sched_droid.csv)
- [Testbed, implementation, and experimental limits](design-document-multinode.md)
