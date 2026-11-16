//! Compact event model. C ABI uses bounded numeric buffers, never Python objects.
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::ffi::CString;
use std::os::raw::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::slice;
use std::sync::Arc;

thread_local! { static ERROR: RefCell<CString> = RefCell::new(CString::new("").unwrap()); }
fn fail(s: impl ToString) {
    ERROR.with(|e| *e.borrow_mut() = CString::new(s.to_string().replace('\0', " ")).unwrap());
}
#[no_mangle]
pub extern "C" fn planner_error() -> *const c_char {
    ERROR.with(|e| e.borrow().as_ptr())
}

// CPython 3.12+ sum uses Neumaier compensation; older runtimes add sequentially.
// Match the caller so rounding-level placement ties retain their original order.
struct FloatSum {
    hi: f64,
    lo: f64,
    compensated: bool,
}
impl FloatSum {
    fn new(compensated: bool) -> Self {
        Self {
            hi: 0.,
            lo: 0.,
            compensated,
        }
    }
    fn add(&mut self, x: f64) {
        let next = self.hi + x;
        if self.compensated {
            self.lo += if self.hi.abs() >= x.abs() {
                (self.hi - next) + x
            } else {
                (x - next) + self.hi
            };
        }
        self.hi = next;
    }
    fn value(&self) -> f64 {
        if self.lo != 0. && self.lo.is_finite() {
            self.hi + self.lo
        } else {
            self.hi
        }
    }
}
#[no_mangle]
pub extern "C" fn planner_abi_version() -> u32 {
    5
}

struct Reader<'a> {
    data: &'a [f64],
    at: usize,
}
impl<'a> Reader<'a> {
    fn f(&mut self) -> Result<f64, String> {
        let x = *self.data.get(self.at).ok_or("truncated buffer")?;
        self.at += 1;
        if !x.is_finite() {
            return Err("nonfinite value".into());
        }
        Ok(x)
    }
    fn u(&mut self) -> Result<usize, String> {
        let x = self.f()?;
        if x < 0. || x.fract() != 0. || x > 9007199254740991. {
            return Err("invalid integer".into());
        }
        Ok(x as usize)
    }
    fn node(&mut self, nodes: usize) -> Result<usize, String> {
        let x = self.u()?;
        if x >= nodes {
            return Err("invalid node".into());
        }
        Ok(x)
    }
    fn end(&self) -> Result<(), String> {
        if self.at != self.data.len() {
            Err("trailing buffer".into())
        } else {
            Ok(())
        }
    }
}
#[derive(Clone)]
struct Input {
    parent: Option<usize>,
    output: usize,
    node: usize,
    size: f64,
}
struct Task {
    inputs: Vec<Input>,
    outputs: Vec<f64>,
    compute: f64,
    iterations: f64,
    work: f64,
    memory: f64,
    // Compute seconds: fixed, per input byte, per largest-input byte and iteration, per output byte.
    coefficients: [f64; 4],
    deps: Vec<usize>,
    successors: Vec<usize>,
}
pub struct Graph {
    tasks: Vec<Task>,
    nodes: usize,
    // Capacity of resource kind * nodes + node, kinds being read, write, tx, rx and cpu.
    caps: Vec<f64>,
    compensated_sum: bool,
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Action {
    task: usize,
    worker: usize,
    targets: Vec<usize>,
}
#[derive(Clone)]
struct Stage {
    remaining: f64,
    mask: Mask,
}
#[derive(Clone)]
struct Flight {
    action: Action,
    stages: Vec<Stage>,
    index: usize,
}
#[derive(Clone)]
struct State {
    time: f64,
    remote: f64,
    unassigned: f64,
    assigned: Vec<f64>,
    memory: f64,
    // Output placements of the completed tasks the caller sent: those unfinished tasks read.
    completed: BTreeMap<usize, [u8; 8]>,
    completed_count: usize,
    // Whether any completed output's size differs from the graph's estimate.
    resized: bool,
    observed_sizes: Arc<BTreeMap<(usize, usize), f64>>,
    running: Vec<Flight>,
    ready: Vec<usize>,
}
#[derive(Default)]
struct Counts {
    dispatch: u64,
    advance: u64,
    candidates: u64,
    profiles: u64,
    clones: u64,
}
// One bit per resource, so KINDS * MAX_NODES must fit in a Mask.
type Mask = u64;
const KINDS: usize = 5;
const MAX_NODES: usize = 12;
impl Graph {
    fn resource(&self, kind: usize, node: usize) -> Mask {
        1 << (kind * self.nodes + node)
    }
    fn cap(&self, kind: usize, node: usize) -> f64 {
        self.caps[kind * self.nodes + node]
    }
    fn mincap(&self, mask: Mask) -> f64 {
        (0..self.caps.len())
            .filter(|i| mask & (1 << i) != 0)
            .map(|i| self.caps[i])
            .fold(f64::INFINITY, f64::min)
    }
    fn read(r: &mut Reader) -> Result<Self, String> {
        let n = r.u()?;
        if n > 1000000 {
            return Err("too many tasks".into());
        }
        let nodes = r.u()?;
        if nodes == 0 || nodes > MAX_NODES {
            return Err(format!(
                "native policies support 1 to {MAX_NODES} nodes, not {nodes}"
            ));
        }
        let mut caps = vec![0.; KINDS * nodes];
        for c in &mut caps {
            *c = r.f()?;
            if *c <= 0. {
                return Err("invalid capacity".into());
            }
        }
        let compensated_sum = r.u()? != 0;
        let mut tasks = Vec::with_capacity(n);
        for _ in 0..n {
            let ni = r.u()?;
            if ni > 1024 {
                return Err("too many inputs".into());
            }
            let mut inputs = Vec::new();
            let mut deps = Vec::new();
            for _ in 0..ni {
                let p = r.f()?;
                let parent = if p == -1. {
                    None
                } else {
                    if p < 0. || p.fract() != 0. || p >= n as f64 {
                        return Err("bad parent".into());
                    }
                    Some(p as usize)
                };
                let output = r.u()?;
                let node = r.node(nodes)?;
                let size = r.f()?;
                if size < 0. {
                    return Err("negative size".into());
                }
                if let Some(p) = parent {
                    if !deps.contains(&p) {
                        deps.push(p)
                    }
                }
                inputs.push(Input {
                    parent,
                    output,
                    node,
                    size,
                });
            }
            let no = r.u()?;
            if no > 8 {
                return Err("native supports at most 8 outputs".into());
            }
            let mut outputs = Vec::new();
            for _ in 0..no {
                let s = r.f()?;
                if s < 0. {
                    return Err("negative size".into());
                }
                outputs.push(s)
            }
            let compute = r.f()?;
            let iterations = r.f()?;
            let work = r.f()?;
            let memory = r.f()?;
            if compute < 0. || work < 0. || memory < 0. {
                return Err("negative work".into());
            }
            let mut coefficients = [0.; 4];
            for c in &mut coefficients {
                *c = r.f()?;
            }
            tasks.push(Task {
                inputs,
                outputs,
                compute,
                iterations,
                work,
                memory,
                coefficients,
                deps,
                successors: Vec::new(),
            });
        }
        for t in &tasks {
            for i in &t.inputs {
                if let Some(p) = i.parent {
                    if i.output >= tasks[p].outputs.len() {
                        return Err("bad output reference".into());
                    }
                }
            }
        }
        for i in 0..n {
            for p in tasks[i].deps.clone() {
                tasks[p].successors.push(i)
            }
        }
        Ok(Self {
            tasks,
            nodes,
            caps,
            compensated_sum,
        })
    }
}
impl State {
    fn read(g: &Graph, r: &mut Reader) -> Result<Self, String> {
        let time = r.f()?;
        let remote = r.f()?;
        let unassigned = r.f()?;
        let mut assigned = vec![0.; g.nodes];
        for a in &mut assigned {
            *a = r.f()?
        }
        let memory = r.f()?;
        let mut completed = BTreeMap::new();
        let mut observed_sizes = BTreeMap::new();
        let completed_count = r.u()?;
        let resized = r.u()? != 0;
        let sent = r.u()?;
        if completed_count > g.tasks.len() || sent > completed_count {
            return Err("bad completed count".into());
        }
        for _ in 0..sent {
            let tid = r.u()?;
            let t = g.tasks.get(tid).ok_or("bad completed task")?;
            if completed.contains_key(&tid) {
                return Err("duplicate completed task".into());
            }
            let mut targets = [0u8; 8];
            for target in targets.iter_mut().take(t.outputs.len()) {
                *target = r.node(g.nodes)? as u8;
            }
            for (index, predicted) in t.outputs.iter().enumerate() {
                let size = r.f()?;
                if size < 0. {
                    return Err("negative observed size".into());
                }
                if size != *predicted {
                    observed_sizes.insert((tid, index), size);
                }
            }
            completed.insert(tid, targets);
        }
        let nr = r.u()?;
        if nr > g.nodes {
            return Err("too many running tasks".into());
        }
        let mut running = Vec::new();
        let mut busy = vec![false; g.nodes];
        for _ in 0..nr {
            let action = read_action(g, r)?.ok_or("running wait")?;
            if busy[action.worker] || completed.contains_key(&action.task) {
                return Err("invalid running state".into());
            }
            busy[action.worker] = true;
            let ns = r.u()?;
            if ns == 0 || ns > 2048 {
                return Err("bad stage count".into());
            }
            let mut stages = Vec::new();
            for _ in 0..ns {
                let remaining = r.f()?;
                let resources = r.u()?;
                if remaining < 0. || resources == 0 || resources > g.caps.len() {
                    return Err("invalid stage".into());
                }
                let mut mask: Mask = 0;
                for _ in 0..resources {
                    let resource = r.u()?;
                    if resource >= g.caps.len() {
                        return Err("invalid stage resource".into());
                    }
                    mask |= 1 << resource
                }
                stages.push(Stage { remaining, mask })
            }
            running.push(Flight {
                action,
                stages,
                index: 0,
            });
        }
        let nready = r.u()?;
        if nready > g.tasks.len() {
            return Err("invalid ready count".into());
        }
        let mut ready = Vec::new();
        for _ in 0..nready {
            let tid = r.u()?;
            if tid >= g.tasks.len()
                || completed.contains_key(&tid)
                || running.iter().any(|f| f.action.task == tid)
                || !g.tasks[tid].deps.iter().all(|p| completed.contains_key(p))
                || ready.last().is_some_and(|v| *v >= tid)
            {
                return Err("invalid ready state".into());
            }
            ready.push(tid)
        }
        Ok(Self {
            time,
            remote,
            unassigned,
            assigned,
            memory,
            completed,
            completed_count,
            resized,
            observed_sizes: Arc::new(observed_sizes),
            running,
            ready,
        })
    }
    fn free(&self) -> Vec<bool> {
        let mut free = vec![true; self.assigned.len()];
        for f in &self.running {
            free[f.action.worker] = false
        }
        free
    }
    fn input_node(&self, i: &Input) -> Result<usize, String> {
        match i.parent {
            None => Ok(i.node),
            Some(p) => self
                .completed
                .get(&p)
                .and_then(|t| t.get(i.output))
                .map(|n| *n as usize)
                .ok_or("unresolved input".into()),
        }
    }
    fn input_size(&self, input: &Input) -> f64 {
        if let Some(parent) = input.parent {
            if let Some(size) = self.observed_sizes.get(&(parent, input.output)) {
                return *size;
            }
        }
        input.size
    }
    fn compute_seconds(&self, task: &Task) -> f64 {
        if !self.resized {
            return task.compute;
        }
        let mut sum = 0.;
        let mut max: f64 = 0.;
        for input in &task.inputs {
            let size = self.input_size(input);
            sum += size;
            max = max.max(size);
        }
        let c = &task.coefficients;
        (c[0] + sum * c[1] + max * task.iterations * c[2] + task.outputs.iter().sum::<f64>() * c[3])
            .max(0.)
    }
    fn stages(&self, g: &Graph, a: &Action) -> Result<Vec<Stage>, String> {
        let t = &g.tasks[a.task];
        let mut out = Vec::with_capacity(t.inputs.len() + 1 + t.outputs.len());
        for i in &t.inputs {
            let n = self.input_node(i)?;
            let mask = g.resource(0, n)
                | if n != a.worker {
                    g.resource(2, n) | g.resource(3, a.worker)
                } else {
                    0
                };
            out.push(Stage {
                remaining: self.input_size(i),
                mask,
            })
        }
        out.push(Stage {
            remaining: self.compute_seconds(t),
            mask: g.resource(4, a.worker),
        });
        for (n, s) in a.targets.iter().zip(&t.outputs) {
            let mask = g.resource(1, *n)
                | if *n != a.worker {
                    g.resource(2, a.worker) | g.resource(3, *n)
                } else {
                    0
                };
            out.push(Stage {
                remaining: *s,
                mask,
            })
        }
        Ok(out)
    }
    fn profile(&self, g: &Graph, a: &Action) -> Result<(f64, f64), String> {
        let t = &g.tasks[a.task];
        let mut seconds = FloatSum::new(g.compensated_sum);
        let mut bytes = 0.;
        for i in &t.inputs {
            let n = self.input_node(i)?;
            let mut rate = g.cap(0, n);
            if n != a.worker {
                rate = rate.min(g.cap(2, n)).min(g.cap(3, a.worker));
                bytes += self.input_size(i)
            }
            seconds.add(self.input_size(i) / rate);
        }
        seconds.add(self.compute_seconds(t) / g.cap(4, a.worker));
        for (n, size) in a.targets.iter().zip(&t.outputs) {
            let mut rate = g.cap(1, *n);
            if *n != a.worker {
                rate = rate.min(g.cap(2, a.worker)).min(g.cap(3, *n));
                bytes += size
            }
            seconds.add(size / rate);
        }
        Ok((seconds.value(), bytes))
    }
    fn dispatch(&mut self, g: &Graph, a: Action, c: &mut Counts) -> Result<(), String> {
        let pos = self
            .ready
            .binary_search(&a.task)
            .map_err(|_| "task not ready")?;
        if !self.free()[a.worker] || g.tasks[a.task].memory > self.memory {
            return Err("infeasible worker/memory".into());
        }
        let stages = self.stages(g, &a)?;
        let (seconds, bytes) = self.profile(g, &a)?;
        c.profiles += 1;
        self.ready.remove(pos);
        self.unassigned -= g.tasks[a.task].work;
        self.remote += bytes;
        self.assigned[a.worker] += seconds;
        self.running.push(Flight {
            action: a,
            stages,
            index: 0,
        });
        c.dispatch += 1;
        self.finish_zero(g);
        Ok(())
    }
    fn finish_zero(&mut self, g: &Graph) {
        let mut finished = Vec::new();
        for f in &mut self.running {
            while f.index < f.stages.len() && f.stages[f.index].remaining <= 1e-9 {
                f.index += 1
            }
            if f.index == f.stages.len() {
                let mut targets = [0u8; 8];
                for (target, node) in targets.iter_mut().zip(&f.action.targets) {
                    *target = *node as u8;
                }
                self.completed.insert(f.action.task, targets);
                self.completed_count += 1;
                finished.push(f.action.task)
            }
        }
        self.running.retain(|f| f.index < f.stages.len());
        for tid in finished {
            for next in &g.tasks[tid].successors {
                if !self.completed.contains_key(next)
                    && !self.running.iter().any(|f| f.action.task == *next)
                    && g.tasks[*next]
                        .deps
                        .iter()
                        .all(|p| self.completed.contains_key(p))
                {
                    if let Err(pos) = self.ready.binary_search(next) {
                        self.ready.insert(pos, *next)
                    }
                }
            }
        }
    }
    fn advance(&mut self, g: &Graph, c: &mut Counts) -> Result<f64, String> {
        self.finish_zero(g);
        if self.running.is_empty() {
            return if self.completed_count == g.tasks.len() {
                Ok(0.)
            } else {
                Err("cannot wait without running task".into())
            };
        }
        let mut usage = vec![0u32; g.caps.len()];
        for f in &self.running {
            let m = f.stages[f.index].mask;
            for (i, u) in usage.iter_mut().enumerate() {
                if m & (1 << i) != 0 {
                    *u += 1
                }
            }
        }
        let mut rates = vec![0.; self.running.len()];
        let mut dt = f64::INFINITY;
        for (j, f) in self.running.iter().enumerate() {
            let s = &f.stages[f.index];
            rates[j] = (0..g.caps.len())
                .filter(|i| s.mask & (1 << i) != 0)
                .map(|i| g.caps[i] / usage[i] as f64)
                .fold(f64::INFINITY, f64::min);
            dt = dt.min(s.remaining / rates[j])
        }
        for (j, f) in self.running.iter_mut().enumerate() {
            let s = &mut f.stages[f.index];
            s.remaining = (s.remaining - dt * rates[j]).max(0.)
        }
        self.time += dt;
        c.advance += 1;
        self.finish_zero(g);
        Ok(dt)
    }
    fn tail(&self, g: &Graph) -> f64 {
        let mut active = FloatSum::new(g.compensated_sum);
        for f in &self.running {
            let mut one = FloatSum::new(g.compensated_sum);
            for s in &f.stages[f.index..] {
                one.add(s.remaining / g.mincap(s.mask));
            }
            active.add(one.value());
        }
        self.time + (self.unassigned.max(0.) + active.value()) / g.nodes as f64
    }
    fn apply(&mut self, g: &Graph, a: Option<Action>, c: &mut Counts) -> Result<(), String> {
        match a {
            Some(a) => self.dispatch(g, a, c),
            None => self.advance(g, c).map(|_| ()),
        }
    }
    fn snapshot(&self, g: &Graph) -> Vec<f64> {
        let mut o = vec![self.time, self.remote, self.tail(g)];
        o.extend(&self.assigned);
        o.push(self.completed_count as f64);
        o.push(self.completed.len() as f64);
        for (tid, targets) in &self.completed {
            o.push(*tid as f64);
            o.extend(
                targets
                    .iter()
                    .take(g.tasks[*tid].outputs.len())
                    .map(|n| *n as f64),
            );
        }
        o.push(self.running.len() as f64);
        o.extend(self.running.iter().map(|f| f.action.task as f64));
        o.push(self.ready.len() as f64);
        o.extend(self.ready.iter().map(|t| *t as f64));
        o
    }
}
fn read_action(g: &Graph, r: &mut Reader) -> Result<Option<Action>, String> {
    let tid = r.f()?;
    if tid == -1. {
        return Ok(None);
    }
    if tid < 0. || tid.fract() != 0. || tid >= g.tasks.len() as f64 {
        return Err("invalid action task".into());
    }
    let task = tid as usize;
    let worker = r.node(g.nodes)?;
    let mut targets = Vec::new();
    for _ in &g.tasks[task].outputs {
        targets.push(r.node(g.nodes)?)
    }
    Ok(Some(Action {
        task,
        worker,
        targets,
    }))
}
unsafe fn reader<'a>(ptr: *const f64, len: usize) -> Result<Reader<'a>, String> {
    if ptr.is_null() || len > 100000000 {
        return Err("invalid buffer".into());
    }
    Ok(Reader {
        data: slice::from_raw_parts(ptr, len),
        at: 0,
    })
}
#[no_mangle]
pub unsafe extern "C" fn planner_graph(data: *const f64, len: usize) -> *mut Graph {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut r = reader(data, len)?;
        let g = Graph::read(&mut r)?;
        r.end()?;
        Ok::<_, String>(g)
    }));
    match result {
        Ok(Ok(g)) => Box::into_raw(Box::new(g)),
        Ok(Err(e)) => {
            fail(e);
            std::ptr::null_mut()
        }
        Err(_) => {
            fail("native panic");
            std::ptr::null_mut()
        }
    }
}
#[no_mangle]
pub unsafe extern "C" fn planner_free(g: *mut Graph) {
    if !g.is_null() {
        drop(Box::from_raw(g))
    }
}
unsafe fn output(values: &[f64], out: *mut f64, capacity: usize) -> Result<isize, String> {
    if out.is_null() || values.len() > capacity {
        return Err("output buffer too small".into());
    }
    std::ptr::copy_nonoverlapping(values.as_ptr(), out, values.len());
    Ok(values.len() as isize)
}
#[no_mangle]
pub unsafe extern "C" fn planner_simulate(
    g: *const Graph,
    data: *const f64,
    len: usize,
    out: *mut f64,
    capacity: usize,
) -> isize {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let g = g.as_ref().ok_or("null graph")?;
        let mut r = reader(data, len)?;
        let mut s = State::read(g, &mut r)?;
        let n = r.u()?;
        let mut c = Counts::default();
        for _ in 0..n {
            s.apply(g, read_action(g, &mut r)?, &mut c)?
        }
        r.end()?;
        output(&s.snapshot(g), out, capacity)
    }));
    match result {
        Ok(Ok(n)) => n,
        Ok(Err(e)) => {
            fail(e);
            -1
        }
        Err(_) => {
            fail("native panic");
            -1
        }
    }
}

use std::time::Instant;
struct Mt {
    words: [u32; 624],
    index: usize,
}
impl Mt {
    fn read(r: &mut Reader) -> Result<Self, String> {
        let mut words = [0; 624];
        for w in &mut words {
            let x = r.u()?;
            if x > u32::MAX as usize {
                return Err("invalid RNG word".into());
            }
            *w = x as u32
        }
        let index = r.u()?;
        if index > 624 {
            return Err("invalid RNG index".into());
        }
        Ok(Self { words, index })
    }
    fn word(&mut self) -> u32 {
        if self.index >= 624 {
            for i in 0..624 {
                let y = (self.words[i] & 0x80000000) | (self.words[(i + 1) % 624] & 0x7fffffff);
                self.words[i] = self.words[(i + 397) % 624]
                    ^ (y >> 1)
                    ^ if y & 1 != 0 { 0x9908b0df } else { 0 };
            }
            self.index = 0
        }
        let mut y = self.words[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c5680;
        y ^= (y << 15) & 0xefc60000;
        y ^= y >> 18;
        y
    }
    fn random(&mut self) -> f64 {
        let a = self.word() >> 5;
        let b = self.word() >> 6;
        (a as f64 * 67108864. + b as f64) / 9007199254740992.
    }
    fn below(&mut self, n: usize) -> usize {
        assert!(n > 0 && n <= u32::MAX as usize);
        let k = usize::BITS - n.leading_zeros();
        loop {
            let x = (self.word() >> (32 - k)) as usize;
            if x < n {
                return x;
            }
        }
    }
    fn write(&self, o: &mut Vec<f64>) {
        o.extend(self.words.iter().map(|w| *w as f64));
        o.push(self.index as f64)
    }
}
#[derive(Clone, Copy)]
struct Objective {
    seconds: f64,
    bytes: f64,
}
impl Objective {
    fn key(&self, s: f64, b: f64) -> (f64, f64) {
        (self.seconds * s + self.bytes * b, b)
    }
}
fn candidates(s: &State, g: &Graph, limit: usize, variants: bool, c: &mut Counts) -> Vec<Action> {
    c.candidates += 1;
    let free = s.free();
    let mut out = Vec::new();
    for tid in s.ready.iter().take(limit) {
        let t = &g.tasks[*tid];
        if t.memory > s.memory {
            continue;
        }
        for (worker, available) in free.iter().enumerate() {
            if !available {
                continue;
            }
            if variants {
                let combinations = g.nodes.pow(t.outputs.len() as u32);
                for code in 0..combinations {
                    let mut targets = vec![0; t.outputs.len()];
                    let mut code = code;
                    for n in targets.iter_mut().rev() {
                        *n = code % g.nodes;
                        code /= g.nodes
                    }
                    out.push(Action {
                        task: *tid,
                        worker,
                        targets,
                    })
                }
            } else {
                out.push(Action {
                    task: *tid,
                    worker,
                    targets: vec![worker; t.outputs.len()],
                })
            }
        }
    }
    out
}
fn actions(s: &State, g: &Graph, c: &mut Counts) -> Result<Vec<Option<Action>>, String> {
    let options = candidates(s, g, 8, true, c);
    let mut ranked = Vec::with_capacity(options.len());
    for a in options {
        let (seconds, bytes) = s.profile(g, &a)?;
        c.profiles += 1;
        ranked.push((seconds, bytes, a))
    }
    ranked.sort_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then(a.1.total_cmp(&b.1))
            .then(a.2.task.cmp(&b.2.task))
            .then(a.2.worker.cmp(&b.2.worker))
            .then(a.2.targets.cmp(&b.2.targets))
    });
    let mut out: Vec<_> = ranked
        .into_iter()
        .take(24)
        .map(|(_, _, a)| Some(a))
        .collect();
    if !s.running.is_empty() {
        out.push(None)
    }
    Ok(out)
}
struct SearchNode {
    state: State,
    action: Option<Action>,
    parent: Option<usize>,
    children: Vec<usize>,
    untried: Option<Vec<Option<Action>>>,
    visits: u64,
    seconds: f64,
    bytes: f64,
}
struct Search {
    iterations: usize,
    horizon: usize,
    budget: f64,
    uniform: bool,
    objective: Objective,
    rng: Mt,
    counts: Counts,
    start: Instant,
    actual: usize,
    partial: usize,
}
impl Search {
    fn expired(&self) -> bool {
        self.start.elapsed().as_secs_f64() >= self.budget
    }
    fn cloned(&mut self, s: &State) -> State {
        self.counts.clones += 1;
        s.clone()
    }
    fn rollout(&mut self, mut s: State, g: &Graph) -> Result<(f64, f64), String> {
        let mut dispatches = 0;
        while s.completed_count < g.tasks.len() && dispatches < self.horizon && !self.expired() {
            let options = candidates(&s, g, 4, self.uniform, &mut self.counts);
            if !options.is_empty() {
                let selected = if self.uniform || self.rng.random() < 0.2 {
                    self.rng.below(options.len())
                } else {
                    let mut best = 0;
                    let mut key = (f64::INFINITY, f64::INFINITY);
                    for (i, a) in options.iter().enumerate() {
                        let current = s.profile(g, a)?;
                        self.counts.profiles += 1;
                        if current < key {
                            best = i;
                            key = current
                        }
                    }
                    best
                };
                s.dispatch(g, options[selected].clone(), &mut self.counts)?;
                dispatches += 1
            } else if !s.running.is_empty() {
                s.advance(g, &mut self.counts)?;
            } else {
                return Err("rollout deadlock".into());
            }
        }
        if s.completed_count < g.tasks.len() && dispatches < self.horizon && self.expired() {
            self.partial += 1
        }
        Ok((
            if s.completed_count == g.tasks.len() {
                s.time
            } else {
                s.tail(g)
            },
            s.remote,
        ))
    }
    fn choose(&mut self, s: State, g: &Graph) -> Result<Option<Action>, String> {
        let feasible = actions(&s, g, &mut self.counts)?;
        if feasible.is_empty() || feasible == [None] {
            return Ok(None);
        }
        let scale = (s.tail(g) - s.time).max(0.001);
        let mut tree = vec![SearchNode {
            state: s,
            action: None,
            parent: None,
            children: Vec::new(),
            untried: Some(feasible.clone()),
            visits: 0,
            seconds: 0.,
            bytes: 0.,
        }];
        for _ in 0..self.iterations {
            if self.expired() {
                break;
            }
            let mut node = 0;
            let mut depth = 0;
            while tree[node].untried.as_ref().is_some_and(|u| u.is_empty())
                && !tree[node].children.is_empty()
                && depth < self.horizon
            {
                let log = (tree[node].visits.max(1) as f64).ln();
                let mut best = tree[node].children[0];
                let mut score = f64::NEG_INFINITY;
                for child in &tree[node].children {
                    let ch = &tree[*child];
                    let mean = self
                        .objective
                        .key(ch.seconds / ch.visits as f64, ch.bytes / ch.visits as f64)
                        .0;
                    let value = -mean / scale + (2. * log / ch.visits as f64).sqrt();
                    if value > score {
                        score = value;
                        best = *child
                    }
                }
                node = best;
                depth += 1;
            }
            if tree[node].untried.is_none() {
                tree[node].untried = Some(if tree[node].state.completed_count == g.tasks.len() {
                    Vec::new()
                } else {
                    actions(&tree[node].state, g, &mut self.counts)?
                });
            }
            if !tree[node].untried.as_ref().unwrap().is_empty() && depth < self.horizon {
                let index = if tree[node].children.is_empty() {
                    0
                } else {
                    self.rng.below(tree[node].untried.as_ref().unwrap().len())
                };
                let action = tree[node].untried.as_mut().unwrap().remove(index);
                let mut future = self.cloned(&tree[node].state);
                future.apply(g, action.clone(), &mut self.counts)?;
                let child = tree.len();
                tree[node].children.push(child);
                tree.push(SearchNode {
                    state: future,
                    action,
                    parent: Some(node),
                    children: Vec::new(),
                    untried: None,
                    visits: 0,
                    seconds: 0.,
                    bytes: 0.,
                });
                node = child;
            }
            let future = self.cloned(&tree[node].state);
            let (seconds, bytes) = self.rollout(future, g)?;
            let mut cursor = Some(node);
            while let Some(n) = cursor {
                let node = &mut tree[n];
                node.visits += 1;
                node.seconds += seconds;
                node.bytes += bytes;
                cursor = node.parent
            }
            self.actual += 1;
        }
        if tree[0].children.is_empty() {
            return Ok(feasible[0].clone());
        }
        let mut best = tree[0].children[0];
        let mut key = (f64::INFINITY, f64::INFINITY);
        for child in &tree[0].children {
            let ch = &tree[*child];
            let current = self
                .objective
                .key(ch.seconds / ch.visits as f64, ch.bytes / ch.visits as f64);
            if current < key {
                key = current;
                best = *child
            }
        }
        Ok(tree[best].action.clone())
    }
}
#[no_mangle]
pub unsafe extern "C" fn planner_search(
    g: *const Graph,
    data: *const f64,
    len: usize,
    out: *mut f64,
    capacity: usize,
) -> isize {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let start = Instant::now();
        let g = g.as_ref().ok_or("null graph")?;
        let mut r = reader(data, len)?;
        let state = State::read(g, &mut r)?;
        let iterations = r.u()?;
        let horizon = r.u()?;
        let budget = r.f()?;
        let uniform = r.u()? != 0;
        let objective = Objective {
            seconds: r.f()?,
            bytes: r.f()?,
        };
        if iterations > 1000000 || horizon > 10000 || budget < 0. {
            return Err("invalid search bound".into());
        }
        let rng = Mt::read(&mut r)?;
        r.end()?;
        let mut search = Search {
            iterations,
            horizon,
            budget,
            uniform,
            objective,
            rng,
            counts: Counts::default(),
            start,
            actual: 0,
            partial: 0,
        };
        let action = search.choose(state, g)?;
        let seconds = start.elapsed().as_secs_f64();
        let mut values = vec![
            search.actual as f64,
            seconds,
            search.counts.dispatch as f64,
            search.counts.advance as f64,
            search.counts.candidates as f64,
            search.counts.profiles as f64,
            search.counts.clones as f64,
            search.partial as f64,
        ];
        match action {
            None => values.push(-1.),
            Some(a) => {
                values.push(a.task as f64);
                values.push(a.worker as f64);
                values.extend(a.targets.iter().map(|n| *n as f64));
            }
        }
        search.rng.write(&mut values);
        output(&values, out, capacity)
    }));
    match result {
        Ok(Ok(n)) => n,
        Ok(Err(e)) => {
            fail(e);
            -1
        }
        Err(_) => {
            fail("native panic");
            -1
        }
    }
}

mod alternatives;
