# Native planner

This Rust crate holds a copy of the event simulation in `cephdag/model.py` and two
searches built on it: beam search (`planner_beam`, in `src/alternatives.rs`) and Monte
Carlo tree search (`planner_search`). Python reaches them through
`cephdag/policies/native.py` with the standard-library `ctypes`; the policies are
`beam-<width>` and `native-mcts-heuristic-<n>` / `native-mcts-uniform-<n>`. The unused
`planner_schedule` and `planner_local` exports were removed when the crate learned more
than three nodes.

The crate has no dependencies. Build it once per platform:

```sh
cargo build --release --offline --manifest-path native/Cargo.toml
```

Python loads `native/target/release/libcephdag_native.so` (`.dylib` on macOS). There is
no Python fallback when the library is missing.

## Limits worth knowing

- The crate models 1 to 12 nodes: the graph buffer carries the node count, and each
  stage's resources are one bit of a `u64` mask, `kind * nodes + node` for five kinds.
  With three nodes it computes exactly what the three-node crate did.
- At most 8 outputs and 1,024 inputs per task.
- Each task carries its own compute coefficients (fixed, per input byte, per largest-input
  byte and iteration, per output byte): its function's `FunctionCost` with no iteration
  term, or the global `ComputeModel`. Once a completed output's real size differs from
  its estimate, the crate recomputes a consumer's compute time from them, as
  `model.Simulation` does.
- `planner_abi_version()` must return 5.
- Byte sizes cross the boundary as f64, so the bridge refuses integers above 2**53.
- Python's `sum()` uses compensated addition from CPython 3.12; the bridge passes a flag
  so the crate adds the same way. Equivalence was checked on CPython 3.9, the lab's
  interpreter.

The C ABI is internal: pointers are owned by the bridge, numeric buffers are bounds
checked, and native errors become `ValueError`. Payload data and Ceph credentials never
cross it. A policy owns its native graph handle; do not share one policy between threads.

## Building inside the lab

Rust 1.85.0 was installed under `/app/native-toolchain` in node 0 earlier (with approval),
so the crate can be built there without network access:

```sh
docker exec -e PATH=/app/native-toolchain/bin:/usr/bin:/bin \
  -e CARGO_HOME=/app/native-toolchain/cargo-home cephdag-n0 \
  cargo build --release --offline --manifest-path /app/native/Cargo.toml
```
