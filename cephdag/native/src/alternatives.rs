//! Beam search over the event simulation in lib.rs.
use super::*;

fn write_action(a: &Option<Action>, out: &mut Vec<f64>) {
    match a {
        None => out.push(-1.),
        Some(a) => {
            out.push(a.task as f64);
            out.push(a.worker as f64);
            out.extend(a.targets.iter().map(|x| *x as f64));
        }
    }
}
fn apply(s: &mut State, g: &Graph, a: &Option<Action>, c: &mut Counts) -> Result<(), String> {
    match a {
        Some(a) => {
            s.dispatch(g, a.clone(), c)?;
        }
        None => {
            s.advance(g, c)?;
        }
    }
    Ok(())
}
fn wrap(result: Result<Result<isize, String>, Box<dyn std::any::Any + Send>>) -> isize {
    match result {
        Ok(Ok(n)) => n,
        Ok(Err(e)) => {
            fail(e);
            -1
        }
        Err(_) => {
            fail("native alternative panic");
            -1
        }
    }
}
#[no_mangle]
pub unsafe extern "C" fn planner_beam(
    g: *const Graph,
    data: *const f64,
    len: usize,
    out: *mut f64,
    capacity: usize,
) -> isize {
    wrap(catch_unwind(AssertUnwindSafe(|| {
        let start = Instant::now();
        let g = g.as_ref().ok_or("null graph")?;
        let mut r = reader(data, len)?;
        let s = State::read(g, &mut r)?;
        let width = r.u()?;
        let depth = r.u()?;
        let budget = r.f()?;
        let objective = Objective {
            seconds: r.f()?,
            bytes: r.f()?,
        };
        r.end()?;
        if width == 0 || width > 1024 || depth > 10000 || budget < 0. {
            return Err("invalid beam bounds".into());
        }
        let mut c = Counts::default();
        let feasible = actions(&s, g, &mut c)?;
        let fallback = feasible.first().cloned().flatten();
        let mut best_action = fallback;
        let mut expansions = 0;
        let mut levels = 0;
        let mut truncated = false;
        // Each item has a complete state and the root action leading to it.
        let mut beam = vec![(s.clone(), None::<Option<Action>>)];
        c.clones += 1;
        for _ in 0..depth {
            let mut next = Vec::new();
            'outer: for (state, first) in &beam {
                if state.completed_count == g.tasks.len() {
                    next.push((state.clone(), first.clone()));
                    c.clones += 1;
                    continue;
                }
                for a in actions(state, g, &mut c)? {
                    if start.elapsed().as_secs_f64() >= budget {
                        truncated = true;
                        break 'outer;
                    }
                    let mut future = state.clone();
                    c.clones += 1;
                    apply(&mut future, g, &a, &mut c)?;
                    expansions += 1;
                    next.push((future, Some(first.clone().unwrap_or(a))));
                }
            }
            if next.is_empty() {
                break;
            }
            next.sort_by(|a, b| {
                objective
                    .key(a.0.tail(g), a.0.remote)
                    .partial_cmp(&objective.key(b.0.tail(g), b.0.remote))
                    .unwrap()
            });
            next.truncate(width);
            best_action = next[0].1.clone().flatten();
            beam = next;
            if !truncated {
                levels += 1;
            }
            if truncated || beam.iter().all(|x| x.0.completed_count == g.tasks.len()) {
                break;
            }
        }
        let mut v = vec![
            expansions as f64,
            levels as f64,
            truncated as u8 as f64,
            start.elapsed().as_secs_f64(),
            c.dispatch as f64,
            c.advance as f64,
            c.candidates as f64,
            c.profiles as f64,
            c.clones as f64,
        ];
        write_action(&best_action, &mut v);
        output(&v, out, capacity)
    })))
}
