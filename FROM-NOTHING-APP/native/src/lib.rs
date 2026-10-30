//! FOCUS native scheduling core: the duty ledger and full-crew selection hot path.
//!
//! This is a line-for-line port of `focus/engine/ledger.py` (DutyLedger), the crew choice of
//! `focus/engine/crew_selection.py` and the boolean holding checks of `focus/eligibility.py`.
//! Every decision, tie-break, error code and message is identical to the Python reference, so a
//! decode through this core yields byte-identical plans. Python stays the rule authority and the
//! independent validator re-checks every plan from source.
//!
//! * `Context`: immutable data shared by every decode of one engine context (windows, pools,
//!   members, holdings, team certificates, travel policy) plus the exact qualification memo.
//! * `Ledger`: one decode's mutable state. Transactions use an undo log, so a rollback restores
//!   exactly the previous state in O(changes).
//! * `Ledger.search`: the decoder's whole time-slot search for one phase (`greedy._search` and `_book`): every eligible
//!   shift, every candidate start, the calendar advance, the crew choice and the booking. A start where fewer qualified
//!   people are free than the crew needs is skipped without a full crew attempt; that attempt would fail anyway, and the
//!   failure reason the decoder reports is recomputed exactly when it is owed.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;
use std::sync::{Arc, Mutex};

const WORK: u8 = 0;
const TRAVEL: u8 = 1;
const TOOL: u8 = 2;
const OTHER: u8 = 3;
const WAIT: u8 = 4;
const KIND_NAMES: [&str; 5] = ["WORK", "TRAVEL", "TOOL", "OTHER", "WAIT"];
const MAX_COMBINATIONS: usize = 2000;

fn kind_of(s: &str) -> PyResult<u8> {
    match s {
        "WORK" => Ok(WORK),
        "TRAVEL" => Ok(TRAVEL),
        "TOOL" => Ok(TOOL),
        "OTHER" => Ok(OTHER),
        "WAIT" => Ok(WAIT),
        _ => Err(PyValueError::new_err(format!("unknown duty kind {s}"))),
    }
}

// ------------------------------------------------------------------------------------------ names

#[derive(Clone, Default)]
struct Interner {
    names: Vec<Arc<str>>,
    ids: HashMap<Arc<str>, u32>,
}

impl Interner {
    fn intern(&mut self, s: &str) -> u32 {
        if let Some(&id) = self.ids.get(s) {
            return id;
        }
        let name: Arc<str> = Arc::from(s);
        let id = self.names.len() as u32;
        self.names.push(name.clone());
        self.ids.insert(name, id);
        id
    }

    fn name(&self, id: u32) -> &Arc<str> {
        &self.names[id as usize]
    }
}

/// Python `repr` of a str (the cases person ids can take: single quotes unless the text holds a single quote only).
fn py_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                let _ = write!(out, "\\x{:02x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn py_repr_bool(b: bool) -> &'static str {
    if b {
        "True"
    } else {
        "False"
    }
}

/// Python `repr` of a tuple of (int, int, bool) tuples.
fn py_repr_segments(segs: &[(i64, i64, bool)]) -> String {
    let parts: Vec<String> = segs.iter().map(|(a, b, ot)| format!("({a}, {b}, {})", py_repr_bool(*ot))).collect();
    match parts.len() {
        0 => "()".to_string(),
        1 => format!("({},)", parts[0]),
        _ => format!("({})", parts.join(", ")),
    }
}

// ------------------------------------------------------------------------------------------ data

#[derive(Clone)]
struct Window {
    pool: u32,
    regular: Vec<(i64, i64)>,
    ot: Vec<(i64, i64)>,
    regular_cap: i64,
    ot_cap: i64,
    open: Vec<(i64, i64)>,
    span: (i64, i64),
}

#[derive(Clone)]
struct Hold {
    active: bool,
    known: bool,
    start: Option<i64>,
    end: i64,
    revoked: Option<i64>,
}

impl Hold {
    fn covers(&self, start: i64, end: i64) -> bool {
        if !self.active || !self.known {
            return false;
        }
        let Some(s) = self.start else { return false };
        if s > start || self.end < end {
            return false;
        }
        !matches!(self.revoked, Some(r) if r < end)
    }
}

#[derive(Clone)]
struct Item {
    id: Arc<str>,
    person: u32,
    start: i64,
    end: i64,
    kind: u8,
    line: Option<i64>,
    origin: Option<i64>,
    known: bool,
    shift: u32,
    pool: Option<u32>,
    aid: Option<Arc<str>>,
    locked: bool,
}

fn cmp_item(a: &Item, b: &Item) -> Ordering {
    (a.start, a.end, &*a.id).cmp(&(b.start, b.end, &*b.id))
}

type Row = (i64, i64, Arc<str>, u32);

#[derive(Clone)]
struct BookingRec {
    aid: Arc<str>,
    kind: u8,
    line: i64,
    shift: u32,
    pool: u32,
    crew: Vec<u32>,
    segments: Vec<(i64, i64, bool)>,
    tools: Vec<Arc<str>>,
}

struct Planned {
    rec: BookingRec,
    new_items: Vec<(u32, Arc<Vec<Item>>)>,
}

struct LErr {
    code: String,
    detail: String,
}

fn err(code: &str, detail: String) -> LErr {
    LErr { code: code.to_string(), detail }
}

enum Undo {
    Shift((u32, u32), Option<Arc<Vec<Item>>>),
    Rows(u32, Option<Arc<Vec<Row>>>),
    Booking(Arc<str>, Option<BookingRec>),
    Active(u32, u32, Option<i64>),
    Tools(Arc<str>, Vec<(i64, i64, Arc<str>)>),
}

struct Static {
    persons: Interner,
    shifts: Interner,
    pools: Interner,
    windows: HashMap<(u32, u32), Window>,
    travel_same: i64,
    travel_changed: i64,
    travel_unknown: Option<i64>,
    pool_caps: HashMap<u32, i64>,
    counted: [bool; 5],
    tool_cap: HashMap<Arc<str>, i64>,
    members: HashMap<u32, Vec<u32>>,
    holdings: HashMap<u32, HashMap<Arc<str>, Vec<Hold>>>,
    team_certs: HashMap<Arc<str>, HashSet<Arc<str>>>,
}

struct Shared {
    data: Static,
    bundles: Mutex<(Vec<Vec<Vec<Arc<str>>>>, HashMap<Vec<Vec<String>>, usize>)>,
    memo: Mutex<HashMap<(u32, usize, i64, i64), bool>>,
    calendar: Mutex<Option<Arc<Calendar>>>,
}

// ------------------------------------------------------------------------------------------ calendar

/// One shift instance (`calendar.ShiftInstance`): the coordinates the time-slot search needs, plus the pool of every
/// team staffed in it (`EngineContext.pool_for`).
struct ShiftRec {
    id: Arc<str>,
    label: String,
    start: i64,
    end: i64,
    regular: Vec<(i64, i64)>,
    cutoff_coord: i64,
    cutoff_instant: i64,
    ot_window: Option<(i64, i64)>,
    pools: HashMap<String, Arc<str>>,
}

impl ShiftRec {
    /// Open regular minutes in [start, t).
    fn coord(&self, t: i64) -> i64 {
        let mut total = 0;
        for &(ws, we) in &self.regular {
            if t <= ws {
                break;
            }
            total += t.min(we) - ws;
        }
        total
    }

    fn in_regular(&self, t: i64) -> bool {
        self.regular.iter().any(|&(ws, we)| ws <= t && t < we)
    }

    fn next_open(&self, t: i64) -> Option<i64> {
        self.regular.iter().find(|&&(_, we)| t < we).map(|&(ws, _)| t.max(ws))
    }

    fn valid_start(&self, t: i64) -> bool {
        self.in_regular(t) && self.coord(t) <= self.cutoff_coord
    }

    /// `ShiftInstance.advance`, step for step.
    fn advance(&self, start: i64, minutes: i64, ot_allowed: i64) -> Option<(i64, Vec<(i64, i64, bool)>)> {
        if minutes <= 0 {
            return None;
        }
        let mut remaining = minutes;
        let mut segments: Vec<(i64, i64, bool)> = Vec::new();
        let mut t = start;
        for &(ws, we) in &self.regular {
            if remaining == 0 {
                break;
            }
            if we <= t {
                continue;
            }
            let s = t.max(ws);
            let take = (we - s).min(remaining);
            if take > 0 {
                segments.push((s, s + take, false));
                remaining -= take;
                t = s + take;
            }
        }
        if remaining > 0 {
            let (os, oe) = match self.ot_window {
                Some(w) if ot_allowed > 0 && remaining <= ot_allowed => w,
                _ => return None,
            };
            let s = t.max(os);
            if s + remaining > oe {
                return None;
            }
            segments.push((s, s + remaining, true));
        }
        if segments.is_empty() {
            return None;
        }
        let mut merged: Vec<(i64, i64, bool)> = Vec::with_capacity(segments.len());
        for seg in segments {
            match merged.last_mut() {
                Some(last) if last.1 == seg.0 && last.2 == seg.2 => last.1 = seg.1,
                _ => merged.push(seg),
            }
        }
        Some((merged[merged.len() - 1].1, merged))
    }
}

/// The calendar of one engine context: each line's eligible shifts in order, the overtime the decoder may try and the
/// purpose that enters assignment ids.
struct Calendar {
    ot_minutes: i64,
    purpose: String,
    lines: HashMap<i64, Vec<Arc<ShiftRec>>>,
}

/// Python `json.dumps(s, ensure_ascii=False)` for a string.
fn json_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `digest_id("ASG", phase_id, start, purpose)`: "ASG" + the first 20 hex digits of SHA-256 over the canonical JSON list.
fn assignment_id(phase_id: &str, start: i64, purpose: &str) -> String {
    let mut payload = String::with_capacity(phase_id.len() + purpose.len() + 24);
    payload.push('[');
    json_str(&mut payload, phase_id);
    let _ = write!(payload, ",{start},");
    json_str(&mut payload, purpose);
    payload.push(']');
    let digest = Sha256::digest(payload.as_bytes());
    let mut out = String::with_capacity(23);
    out.push_str("ASG");
    for byte in digest.iter().take(10) {
        let _ = write!(out, "{byte:02x}");
    }
    out
}

impl Shared {
    fn holds(&self, person: u32, cert: &str, start: i64, end: i64) -> bool {
        self.data
            .holdings
            .get(&person)
            .and_then(|certs| certs.get(cert))
            .map_or(false, |items| items.iter().any(|h| h.covers(start, end)))
    }

    fn bundle_held(&self, person: u32, groups: &[Vec<Arc<str>>], start: i64, end: i64) -> bool {
        groups.iter().all(|group| group.iter().any(|cert| self.holds(person, cert, start, end)))
    }

    /// `EngineContext.qualifies_within` (window given) or `EngineContext.qualifies` (no window), exactly.
    fn qualifies(&self, person: u32, bundle: usize, groups: &[Vec<Arc<str>>], window: Option<(i64, i64)>, start: i64, end: i64) -> bool {
        if groups.is_empty() {
            return true;
        }
        if let Some((lo, hi)) = window {
            if lo <= start && end <= hi {
                let key = (person, bundle, lo, hi);
                let cached = self.memo.lock().unwrap().get(&key).copied();
                let whole = match cached {
                    Some(v) => v,
                    None => {
                        let v = self.bundle_held(person, groups, lo, hi);
                        self.memo.lock().unwrap().insert(key, v);
                        v
                    }
                };
                if whole {
                    return true;
                }
            }
        }
        self.bundle_held(person, groups, start, end)
    }

    /// `len(index.usable_certs(person, start, end, team_certs.get(team, frozenset())))`.
    fn usable(&self, person: u32, team: &str, start: i64, end: i64) -> i64 {
        let Some(wanted) = self.data.team_certs.get(team) else { return 0 };
        let Some(certs) = self.data.holdings.get(&person) else { return 0 };
        certs
            .iter()
            .filter(|(cert, items)| wanted.contains(&**cert) && items.iter().any(|h| h.covers(start, end)))
            .count() as i64
    }
}

fn overlap(a: i64, b: i64, windows: &[(i64, i64)]) -> i64 {
    windows.iter().map(|&(ws, we)| (b.min(we) - a.max(ws)).max(0)).sum()
}

fn inside(a: i64, b: i64, windows: &[(i64, i64)]) -> bool {
    overlap(a, b, windows) == b - a
}

/// Python `bisect.bisect_right(rows, x, key=end)`, step for step (identical even if rows were not end-sorted).
fn bisect_right_end(rows: &[Row], x: i64) -> usize {
    let (mut lo, mut hi) = (0usize, rows.len());
    while lo < hi {
        let mid = (lo + hi) / 2;
        if x < rows[mid].1 {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    lo
}

// ------------------------------------------------------------------------------------------ context

#[pyclass(module = "focus_native")]
struct Context {
    shared: Arc<Shared>,
}

type WindowRow = (String, String, String, Vec<(i64, i64)>, Vec<(i64, i64)>, i64, i64);
type HoldingRow = (String, String, bool, bool, Option<i64>, i64, Option<i64>);

#[pymethods]
impl Context {
    #[new]
    #[pyo3(signature = (windows, travel, pool_caps, counted, tool_capacity, members, holdings, team_certs))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        windows: Vec<WindowRow>,
        travel: (i64, i64, Option<i64>),
        pool_caps: Vec<(String, i64)>,
        counted: Vec<String>,
        tool_capacity: Vec<(String, i64)>,
        members: Vec<(String, Vec<String>)>,
        holdings: Vec<HoldingRow>,
        team_certs: Vec<(String, Vec<String>)>,
    ) -> PyResult<Self> {
        let mut persons = Interner::default();
        let mut shifts = Interner::default();
        let mut pools = Interner::default();
        let mut win = HashMap::with_capacity(windows.len());
        for (person, shift, pool, regular, ot, regular_cap, ot_cap) in windows {
            let mut open: Vec<(i64, i64)> = regular.iter().chain(ot.iter()).copied().collect();
            open.sort();
            let span = (
                open.iter().map(|w| w.0).min().unwrap_or(0),
                open.iter().map(|w| w.1).max().unwrap_or(0),
            );
            let key = (persons.intern(&person), shifts.intern(&shift));
            win.insert(key, Window { pool: pools.intern(&pool), regular, ot, regular_cap, ot_cap, open, span });
        }
        let mut caps = HashMap::new();
        for (pool, cap) in pool_caps {
            caps.insert(pools.intern(&pool), cap);
        }
        let mut counted_mask = [false; 5];
        for k in counted {
            counted_mask[kind_of(&k)? as usize] = true;
        }
        let mut mem = HashMap::new();
        for (pool, people) in members {
            let pid = pools.intern(&pool);
            let ids = people.iter().map(|p| persons.intern(p)).collect();
            mem.insert(pid, ids);
        }
        let mut held: HashMap<u32, HashMap<Arc<str>, Vec<Hold>>> = HashMap::new();
        for (person, cert, active, known, start, end, revoked) in holdings {
            let pid = persons.intern(&person);
            held.entry(pid).or_default().entry(Arc::from(cert.as_str())).or_default().push(Hold { active, known, start, end, revoked });
        }
        let teams: HashMap<Arc<str>, HashSet<Arc<str>>> = team_certs
            .into_iter()
            .map(|(team, certs)| (Arc::from(team.as_str()), certs.iter().map(|c| Arc::from(c.as_str())).collect()))
            .collect();
        let data = Static {
            persons,
            shifts,
            pools,
            windows: win,
            travel_same: travel.0,
            travel_changed: travel.1,
            travel_unknown: travel.2,
            pool_caps: caps,
            counted: counted_mask,
            tool_cap: tool_capacity.into_iter().map(|(t, c)| (Arc::from(t.as_str()), c)).collect(),
            members: mem,
            holdings: held,
            team_certs: teams,
        };
        Ok(Context {
            shared: Arc::new(Shared {
                data,
                bundles: Mutex::new((Vec::new(), HashMap::new())),
                memo: Mutex::new(HashMap::new()),
                calendar: Mutex::new(None),
            }),
        })
    }

    /// Register the calendar the time-slot search reads: every shift instance (id, label, start, end, regular windows,
    /// no-start cutoff in coordinates and as an instant, OT window, (team, pool) pairs) and each line's eligible shift
    /// ids in calendar order. Registering again replaces it.
    #[allow(clippy::type_complexity)]
    fn register_calendar(
        &self,
        ot_minutes: i64,
        purpose: String,
        shifts: Vec<(String, String, i64, i64, Vec<(i64, i64)>, i64, i64, Option<(i64, i64)>, Vec<(String, String)>)>,
        lines: Vec<(i64, Vec<String>)>,
    ) -> PyResult<()> {
        let mut by_id: HashMap<String, Arc<ShiftRec>> = HashMap::with_capacity(shifts.len());
        for (id, label, start, end, regular, cutoff_coord, cutoff_instant, ot_window, pools) in shifts {
            let rec = ShiftRec {
                id: Arc::from(id.as_str()),
                label,
                start,
                end,
                regular,
                cutoff_coord,
                cutoff_instant,
                ot_window,
                pools: pools.into_iter().map(|(team, pool)| (team, Arc::from(pool.as_str()))).collect(),
            };
            by_id.insert(id, Arc::new(rec));
        }
        let mut by_line = HashMap::with_capacity(lines.len());
        for (line, ids) in lines {
            let mut list = Vec::with_capacity(ids.len());
            for id in ids {
                list.push(by_id.get(&id).cloned().ok_or_else(|| PyValueError::new_err(format!("line {line}: unregistered shift {id}")))?);
            }
            by_line.insert(line, list);
        }
        *self.shared.calendar.lock().unwrap() = Some(Arc::new(Calendar { ot_minutes, purpose, lines: by_line }));
        Ok(())
    }

    /// Register a requirement bundle (AND groups of OR alternatives); equal content returns the same id.
    fn register_bundle(&self, groups: Vec<Vec<String>>) -> usize {
        let mut reg = self.shared.bundles.lock().unwrap();
        if let Some(&id) = reg.1.get(&groups) {
            return id;
        }
        let id = reg.0.len();
        reg.0.push(groups.iter().map(|g| g.iter().map(|c| Arc::from(c.as_str())).collect()).collect());
        reg.1.insert(groups, id);
        id
    }

    /// A new, empty ledger for one decode over this context.
    fn ledger(&self) -> Ledger {
        let d = &self.shared.data;
        let tool_res = d.tool_cap.keys().map(|t| (t.clone(), Vec::new())).collect();
        Ledger {
            shared: self.shared.clone(),
            persons: d.persons.clone(),
            shifts: d.shifts.clone(),
            pools: d.pools.clone(),
            shift_items: HashMap::new(),
            rows: HashMap::new(),
            bookings: HashMap::new(),
            active: HashMap::new(),
            tool_res,
            problems: Vec::new(),
            undo: Vec::new(),
            marks: Vec::new(),
            pending: HashMap::new(),
            next_handle: 1,
        }
    }
}

// ------------------------------------------------------------------------------------------ ledger

#[pyclass(module = "focus_native")]
struct Ledger {
    shared: Arc<Shared>,
    persons: Interner,
    shifts: Interner,
    pools: Interner,
    shift_items: HashMap<(u32, u32), Arc<Vec<Item>>>,
    rows: HashMap<u32, Arc<Vec<Row>>>,
    bookings: HashMap<Arc<str>, BookingRec>,
    active: HashMap<u32, HashMap<u32, i64>>,
    tool_res: HashMap<Arc<str>, Vec<(i64, i64, Arc<str>)>>,
    problems: Vec<String>,
    undo: Vec<Undo>,
    marks: Vec<usize>,
    pending: HashMap<u64, Planned>,
    next_handle: u64,
}

type ItemTuple = (String, String, i64, i64, String, Option<i64>, Option<i64>, bool, String, Option<String>, Option<String>, bool);
type BookingTuple = (String, String, i64, String, String, Vec<String>, Vec<(i64, i64, bool)>, Vec<String>);
type ErrTuple = Option<(String, String)>;
type PrefKey = (i64, i64, i64, String);
type ChooseTuple = (Option<Vec<String>>, Option<u64>, usize, usize, Vec<(String, PrefKey)>, String);

impl Ledger {
    fn data(&self) -> &Static {
        &self.shared.data
    }

    fn pname(&self, id: u32) -> &str {
        self.persons.name(id)
    }

    fn sname(&self, id: u32) -> &str {
        self.shifts.name(id)
    }

    fn poolname(&self, id: u32) -> &str {
        self.pools.name(id)
    }

    fn log(&mut self, entry: Undo) {
        if !self.marks.is_empty() {
            self.undo.push(entry);
        }
    }

    fn items_of(&self, person: u32, shift: u32) -> &[Item] {
        self.shift_items.get(&(person, shift)).map_or(&[], |v| v.as_slice())
    }

    fn item_tuple(&self, i: &Item) -> ItemTuple {
        (
            i.id.to_string(),
            self.pname(i.person).to_string(),
            i.start,
            i.end,
            KIND_NAMES[i.kind as usize].to_string(),
            i.line,
            i.origin,
            i.known,
            self.sname(i.shift).to_string(),
            i.pool.map(|p| self.poolname(p).to_string()),
            i.aid.as_ref().map(|a| a.to_string()),
            i.locked,
        )
    }

    fn booking_tuple(&self, b: &BookingRec) -> BookingTuple {
        (
            b.aid.to_string(),
            KIND_NAMES[b.kind as usize].to_string(),
            b.line,
            self.sname(b.shift).to_string(),
            self.poolname(b.pool).to_string(),
            b.crew.iter().map(|&p| self.pname(p).to_string()).collect(),
            b.segments.clone(),
            b.tools.iter().map(|t| t.to_string()).collect(),
        )
    }

    fn travel_minutes(&self, origin: Option<i64>, known: bool, target: i64) -> Option<i64> {
        let d = self.data();
        if !known {
            return d.travel_unknown;
        }
        Some(if origin == Some(target) { d.travel_same } else { d.travel_changed })
    }

    fn active_set(&self, pool: u32) -> HashSet<u32> {
        self.active.get(&pool).map_or_else(HashSet::new, |c| c.iter().filter(|(_, &n)| n > 0).map(|(&p, _)| p).collect())
    }

    // -------------------------------------------------------------- internals (ledger.py)

    fn replace(&mut self, person: u32, shift: u32, items: Arc<Vec<Item>>) {
        let key = (person, shift);
        let old = if items.is_empty() { self.shift_items.remove(&key) } else { self.shift_items.insert(key, items.clone()) };
        self.log(Undo::Shift(key, old));
        let mut rows: Vec<Row> = self.rows.get(&person).map_or_else(Vec::new, |r| r.iter().filter(|r| r.3 != shift).cloned().collect());
        rows.extend(items.iter().map(|i| (i.start, i.end, i.id.clone(), shift)));
        rows.sort();
        let old_rows = self.rows.insert(person, Arc::new(rows));
        self.log(Undo::Rows(person, old_rows));
    }

    fn bump(&mut self, pool: u32, person: u32, delta: i64) {
        let counts = self.active.entry(pool).or_default();
        let old = counts.get(&person).copied();
        counts.insert(person, old.unwrap_or(0) + delta);
        self.log(Undo::Active(pool, person, old));
    }

    fn check_cross_shift(&self, person: u32, shift: u32, spans: &[(i64, i64)]) -> Option<LErr> {
        let rows = self.rows.get(&person)?;
        for &(start, end) in spans {
            let mut idx = bisect_right_end(rows, start);
            while idx < rows.len() && rows[idx].0 < end {
                let r = &rows[idx];
                if r.3 != shift {
                    return Some(err(
                        "PERSON_OVERLAP",
                        format!("{} is busy over [{},{}) in {} (labels never hide overlap)", self.pname(person), r.0, r.1, self.sname(r.3)),
                    ));
                }
                idx += 1;
            }
        }
        None
    }

    fn derive(&self, person: u32, window_shift: u32, w: &Window, items: Vec<Item>) -> Result<Vec<Item>, LErr> {
        let mut located: Vec<Item> = items.into_iter().filter(|i| i.kind != TRAVEL).collect();
        located.sort_by(cmp_item);
        let mut out: Vec<Item> = Vec::with_capacity(located.len() * 2);
        let mut prev: Option<usize> = None;
        for idx in 0..located.len() {
            let item = &located[idx];
            if let Some(p) = prev {
                let pv = &located[p];
                if !item.locked {
                    if let Some(line) = item.line {
                        let Some(minutes) = self.travel_minutes(pv.line, pv.known, line) else {
                            return Err(err("TRAVEL_ORIGIN_UNKNOWN", format!("{}: no bounded policy for travel after {}", self.pname(person), pv.id)));
                        };
                        if minutes > 0 {
                            let mut slot = None;
                            for &(ws, we) in &w.open {
                                let s = pv.end.max(ws);
                                if s + minutes <= we.min(item.start) {
                                    slot = Some((s, s + minutes));
                                    break;
                                }
                            }
                            let Some((s, e)) = slot else {
                                return Err(err(
                                    "TRAVEL_INFEASIBLE",
                                    format!(
                                        "{} cannot reach line {} by {} ({} min transfer after {} through open windows)",
                                        self.pname(person),
                                        line,
                                        item.start,
                                        minutes,
                                        pv.end
                                    ),
                                ));
                            };
                            out.push(Item {
                                id: Arc::from(format!("T:{}", item.id).as_str()),
                                person,
                                start: s,
                                end: e,
                                kind: TRAVEL,
                                line: item.line,
                                origin: pv.line,
                                known: true,
                                shift: window_shift,
                                pool: item.pool,
                                aid: item.aid.clone(),
                                locked: false,
                            });
                        }
                    }
                }
            }
            out.push(located[idx].clone());
            prev = Some(idx);
        }
        out.sort_by(cmp_item);
        for pair in out.windows(2) {
            if pair[1].start < pair[0].end {
                return Err(err("PERSON_OVERLAP", format!("{}: {} overlaps {}", self.pname(person), pair[0].id, pair[1].id)));
            }
        }
        Ok(out)
    }

    fn check_windows(&self, person: u32, w: &Window, items: &[Item]) -> Option<LErr> {
        for i in items {
            if !i.locked && !inside(i.start, i.end, &w.open) {
                return Some(err(
                    "OUTSIDE_ADMITTED_WINDOW",
                    format!("{} {} [{},{}) is outside open/authorized windows", self.pname(person), KIND_NAMES[i.kind as usize], i.start, i.end),
                ));
            }
        }
        None
    }

    fn budget(w: &Window, items: &[Item]) -> (i64, i64) {
        let (mut regular, mut ot) = (0i64, 0i64);
        let (span_start, span_end) = w.span;
        for i in items {
            let in_ot = overlap(i.start, i.end, &w.ot);
            if i.locked {
                regular += (i.end.min(span_end) - i.start.max(span_start)).max(0) - in_ot;
            } else {
                regular += overlap(i.start, i.end, &w.regular);
            }
            ot += in_ot;
        }
        (regular, ot)
    }

    fn check_budget(&self, person: u32, shift: u32, w: &Window, items: &[Item]) -> Option<LErr> {
        let (regular, ot) = Self::budget(w, items);
        if regular > w.regular_cap {
            return Some(err("REGULAR_CAP", format!("{} regular duty {} > {} in {}", self.pname(person), regular, w.regular_cap, self.sname(shift))));
        }
        if ot > w.ot_cap {
            return Some(err("OT_CAP", format!("{} overtime {} > authorized {} in {}", self.pname(person), ot, w.ot_cap, self.sname(shift))));
        }
        None
    }

    fn person_conflict_inner(&self, person: u32, shift: u32, pool: u32, segs: &[(i64, i64, bool)]) -> Option<LErr> {
        let Some(w) = self.data().windows.get(&(person, shift)) else {
            return Some(err("PERSON_NOT_AVAILABLE", format!("{} has no admitted window in {}", self.pname(person), self.sname(shift))));
        };
        if w.pool != pool {
            return Some(err("POOL_MISMATCH", format!("{} belongs to {}, not {}", self.pname(person), self.poolname(w.pool), self.poolname(pool))));
        }
        let spans: Vec<(i64, i64)> = segs.iter().map(|&(a, b, _)| (a, b)).collect();
        if let Some(e) = self.check_cross_shift(person, shift, &spans) {
            return Some(e);
        }
        for i in self.items_of(person, shift) {
            if i.kind == TRAVEL {
                continue;
            }
            for &(a, b, _) in segs {
                if i.start < b && a < i.end {
                    return Some(err(
                        "PERSON_OVERLAP",
                        format!("{} already has {} {} over [{},{})", self.pname(person), KIND_NAMES[i.kind as usize], i.id, i.start, i.end),
                    ));
                }
            }
        }
        None
    }

    fn cap_problem_inner(&self, pool: u32, crew: &[u32], kind: u8, active: Option<&HashSet<u32>>) -> Option<LErr> {
        let Some(&cap) = self.data().pool_caps.get(&pool) else {
            return Some(err("POOL_UNKNOWN", self.poolname(pool).to_string()));
        };
        let owned;
        let act = match active {
            Some(a) => a,
            None => {
                owned = self.active_set(pool);
                &owned
            }
        };
        let extra = crew.iter().filter(|p| !act.contains(*p)).collect::<HashSet<_>>().len();
        let heads = (act.len() + extra) as i64;
        if self.data().counted[kind as usize] && heads > cap {
            return Some(err("ACTIVATION_CAP", format!("pool {} would activate {} people > cap {}", self.poolname(pool), heads, cap)));
        }
        None
    }

    fn tool_problem(&self, tool: &Arc<str>, start: i64, end: i64) -> Option<String> {
        let Some(&cap) = self.data().tool_cap.get(tool) else {
            return Some(format!("TOOL_UNKNOWN: {tool} has no sourced capacity"));
        };
        let busy = self.tool_res.get(tool).map_or(0, |r| r.iter().filter(|(s, e, _)| *s < end && start < *e).count()) as i64;
        if busy < cap {
            None
        } else {
            Some(format!("TOOL_UNAVAILABLE: {tool} checkout busy over [{start},{end})"))
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn plan_inner(
        &self,
        aid: &str,
        crew: &[u32],
        line: i64,
        shift: u32,
        pool: u32,
        segs: &[(i64, i64, bool)],
        kind: u8,
        tools: &[Arc<str>],
        active: Option<&HashSet<u32>>,
    ) -> Result<Planned, LErr> {
        if self.bookings.contains_key(aid) {
            return Err(err("ASSIGNMENT_EXISTS", aid.to_string()));
        }
        if crew.is_empty() {
            return Err(err("CREW_EMPTY", "a booking needs at least one person".to_string()));
        }
        if crew.iter().collect::<HashSet<_>>().len() != crew.len() {
            let names: Vec<String> = crew.iter().map(|&p| py_repr_str(self.pname(p))).collect();
            return Err(err("CREW_DUPLICATE_PERSON", format!("[{}]", names.join(", "))));
        }
        if segs.is_empty() || segs.iter().any(|&(a, b, _)| b <= a) {
            return Err(err("SEGMENTS_INVALID", py_repr_segments(segs)));
        }
        let (start, end) = (segs[0].0, segs[segs.len() - 1].1);
        for tool in tools {
            if let Some(problem) = self.tool_problem(tool, start, end) {
                let code = problem.split(':').next().unwrap_or("").to_string();
                return Err(LErr { code, detail: problem });
            }
        }
        let aid_arc: Arc<str> = Arc::from(aid);
        let mut new_items = Vec::with_capacity(crew.len());
        for &person in crew {
            if let Some(e) = self.person_conflict_inner(person, shift, pool, segs) {
                return Err(e);
            }
            let w = &self.data().windows[&(person, shift)];
            let mut items: Vec<Item> = self.items_of(person, shift).iter().filter(|i| i.kind != TRAVEL).cloned().collect();
            for (n, &(a, b, _)) in segs.iter().enumerate() {
                items.push(Item {
                    id: Arc::from(format!("{}#{}#{}", aid, self.pname(person), n).as_str()),
                    person,
                    start: a,
                    end: b,
                    kind,
                    line: Some(line),
                    origin: None,
                    known: true,
                    shift,
                    pool: Some(pool),
                    aid: Some(aid_arc.clone()),
                    locked: false,
                });
            }
            items.sort_by(cmp_item);
            let items = self.derive(person, shift, w, items)?;
            if let Some(e) = self.check_windows(person, w, &items) {
                return Err(e);
            }
            if let Some(e) = self.check_budget(person, shift, w, &items) {
                return Err(e);
            }
            new_items.push((person, Arc::new(items)));
        }
        if let Some(e) = self.cap_problem_inner(pool, crew, kind, active) {
            return Err(e);
        }
        Ok(Planned {
            rec: BookingRec { aid: aid_arc, kind, line, shift, pool, crew: crew.to_vec(), segments: segs.to_vec(), tools: tools.to_vec() },
            new_items,
        })
    }

    fn commit_planned(&mut self, planned: Planned) {
        let Planned { rec, new_items } = planned;
        for (person, items) in new_items {
            self.replace(person, rec.shift, items);
        }
        if self.data().counted[rec.kind as usize] {
            for &person in &rec.crew {
                self.bump(rec.pool, person, 1);
            }
        }
        let (start, end) = (rec.segments[0].0, rec.segments[rec.segments.len() - 1].1);
        for tool in rec.tools.clone() {
            let old = self.tool_res.get(&tool).cloned().unwrap_or_default();
            self.tool_res.entry(tool.clone()).or_default().push((start, end, rec.aid.clone()));
            self.log(Undo::Tools(tool, old));
        }
        let old = self.bookings.insert(rec.aid.clone(), rec.clone());
        self.log(Undo::Booking(rec.aid, old));
    }

    fn store(&mut self, planned: Planned) -> u64 {
        let handle = self.next_handle;
        self.next_handle += 1;
        if self.pending.len() > 8192 {
            let floor = handle.saturating_sub(4096);
            self.pending.retain(|h, _| *h >= floor);
        }
        self.pending.insert(handle, planned);
        handle
    }

    fn pref_key(&self, person: u32, team: &str, line: i64, shift: u32, start: i64, end: i64) -> PrefKey {
        let usable = self.shared.usable(person, team, start, end);
        let last = self.items_of(person, shift).iter().filter(|i| i.kind != TRAVEL && i.end <= start).last();
        let affinity = if matches!(last, Some(i) if i.line == Some(line)) { 0 } else { 1 };
        let minutes = match self.data().windows.get(&(person, shift)) {
            Some(w) => {
                let (regular, ot) = Self::budget(w, self.items_of(person, shift));
                regular + ot
            }
            None => 0,
        };
        (usable, affinity, minutes, self.pname(person).to_string())
    }

    fn intern_tools(tools: Vec<String>) -> Vec<Arc<str>> {
        tools.iter().map(|t| Arc::from(t.as_str())).collect()
    }

    fn bundle_groups(&self, bundle: usize) -> PyResult<Vec<Vec<Arc<str>>>> {
        let reg = self.shared.bundles.lock().unwrap();
        reg.0.get(bundle).cloned().ok_or_else(|| PyValueError::new_err(format!("unregistered bundle {bundle}")))
    }

    /// `crew_selection.choose_crew` without an exact crew. The planned booking is returned, not stored.
    #[allow(clippy::too_many_arguments, unused_assignments)]
    fn choose_inner(
        &self,
        aid: &str,
        line: i64,
        team: &str,
        s: u32,
        q: u32,
        count: i64,
        bundle: usize,
        groups: &[Vec<Arc<str>>],
        window: Option<(i64, i64)>,
        segments: &[(i64, i64, bool)],
        k: u8,
        tools: &[Arc<str>],
    ) -> Result<Chosen, ChooseFail> {
        if count <= 0 {
            let reason = "CREW_DEMAND_UNAVAILABLE: no sourced positive crew demand (never clamped to 1)".to_string();
            return Err(ChooseFail { reason, tried: 0, qualified: 0 });
        }
        let need = count as usize;
        let (start, end) = (segments[0].0, segments[segments.len() - 1].1);
        let members: &[u32] = self.data().members.get(&q).map_or(&[], |v| v.as_slice());
        let qualified: Vec<u32> = members.iter().copied().filter(|&m| self.shared.qualifies(m, bundle, groups, window, start, end)).collect();
        if qualified.len() < need {
            let reason = format!("CREW_UNQUALIFIED: {} qualified of {} demanded in {}", qualified.len(), count, self.poolname(q));
            return Err(ChooseFail { reason, tried: 0, qualified: qualified.len() });
        }
        let active = self.active_set(q);
        let mut feasible: Vec<u32> = Vec::new();
        let mut last_problem = String::new();
        for &person in &qualified {
            if let Some(e) = self.person_conflict_inner(person, s, q, segments) {
                last_problem = format!("{}: {}", e.code, e.detail);
                continue;
            }
            let probe = format!("{}?{}", aid, self.pname(person));
            match self.plan_inner(&probe, &[person], line, s, q, segments, k, tools, Some(&active)) {
                Ok(_) => feasible.push(person),
                Err(e) => {
                    if e.code != "ACTIVATION_CAP" {
                        last_problem = format!("{}: {}", e.code, e.detail);
                        continue;
                    }
                    feasible.push(person);
                }
            }
        }
        if feasible.len() < need {
            let mut reason = format!("CREW_UNAVAILABLE: {} of {} people free over the interval", feasible.len(), count);
            if !last_problem.is_empty() {
                let _ = write!(reason, " ({last_problem})");
            }
            return Err(ChooseFail { reason, tried: 0, qualified: qualified.len() });
        }
        let mut keyed: Vec<(PrefKey, u32)> = feasible.iter().map(|&p| (self.pref_key(p, team, line, s, start, end), p)).collect();
        keyed.sort_by(|a, b| a.0.cmp(&b.0));
        let mut ordered: Vec<u32> = keyed.iter().map(|(_, p)| *p).collect();
        let room = self.data().pool_caps.get(&q).copied().unwrap_or(0) - active.len() as i64;
        if room < count {
            let (first, second): (Vec<u32>, Vec<u32>) = ordered.iter().copied().partition(|p| active.contains(p));
            ordered = first.into_iter().chain(second).collect();
        }
        let n = ordered.len();
        let mut idx: Vec<usize> = (0..need).collect();
        let mut tried = 0usize;
        let mut last_cap = String::new();
        loop {
            tried += 1;
            if tried > MAX_COMBINATIONS {
                let reason = format!("CREW_SEARCH_BOUND: more than {MAX_COMBINATIONS} combinations; no crew asserted");
                return Err(ChooseFail { reason, tried, qualified: qualified.len() });
            }
            let crew: Vec<u32> = idx.iter().map(|&i| ordered[i]).collect();
            if let Some(e) = self.cap_problem_inner(q, &crew, k, Some(&active)) {
                last_cap = format!("{}: {}", e.code, e.detail);
            } else {
                match self.plan_inner(aid, &crew, line, s, q, segments, k, tools, None) {
                    Ok(planned) => return Ok(Chosen { crew, planned, tried, qualified: qualified.len(), keyed }),
                    Err(e) => last_cap = format!("{}: {}", e.code, e.detail),
                }
            }
            // next combination in itertools.combinations order
            let mut i = need;
            loop {
                if i == 0 {
                    let reason = if last_cap.is_empty() { "NO_FEASIBLE_CREW".to_string() } else { last_cap };
                    return Err(ChooseFail { reason, tried, qualified: qualified.len() });
                }
                i -= 1;
                if idx[i] != i + n - need {
                    break;
                }
            }
            idx[i] += 1;
            for j in i + 1..need {
                idx[j] = idx[j - 1] + 1;
            }
        }
    }

    /// True exactly when `person_conflict_inner` finds nothing (no message is built).
    fn conflict_free(&self, person: u32, shift: u32, pool: u32, segs: &[(i64, i64, bool)]) -> bool {
        match self.data().windows.get(&(person, shift)) {
            Some(w) if w.pool == pool => {}
            _ => return false,
        }
        if let Some(rows) = self.rows.get(&person) {
            for &(start, end, _) in segs {
                let mut idx = bisect_right_end(rows, start);
                while idx < rows.len() && rows[idx].0 < end {
                    if rows[idx].3 != shift {
                        return false;
                    }
                    idx += 1;
                }
            }
        }
        !self.items_of(person, shift).iter().any(|i| i.kind != TRAVEL && segs.iter().any(|&(a, b, _)| i.start < b && a < i.end))
    }

    /// A transfer slot as `derive` looks for it: `minutes` inside one open window between `after` and `before`.
    fn has_slot(w: &Window, after: i64, before: i64, minutes: i64) -> bool {
        w.open.iter().any(|&(ws, we)| after.max(ws) + minutes <= we.min(before))
    }

    /// False only when booking `person` alone on the segments is certain to fail in `plan_inner`: a conflict
    /// (`person_conflict_inner`), a segment outside the person's open windows (`check_windows`), a missing transfer slot
    /// into or out of the new work (`derive`, for the two pairs the new work creates), or regular or overtime minutes over
    /// the caps even before transfers are added (`check_budget`; transfers only add minutes).
    fn could_book(&self, person: u32, shift: u32, pool: u32, line: i64, segs: &[(i64, i64, bool)]) -> bool {
        let Some(w) = self.data().windows.get(&(person, shift)) else { return false };
        if w.pool != pool || !self.conflict_free(person, shift, pool, segs) {
            return false;
        }
        if segs.iter().any(|&(a, b, _)| !inside(a, b, &w.open)) {
            return false;
        }
        let (start, end) = (segs[0].0, segs[segs.len() - 1].1);
        let items = self.items_of(person, shift);
        let mut before: Option<&Item> = None;
        let mut after: Option<&Item> = None;
        let (mut regular, mut ot) = (0i64, 0i64);
        let (span_start, span_end) = w.span;
        for i in items {
            if i.kind == TRAVEL {
                continue;
            }
            let in_ot = overlap(i.start, i.end, &w.ot);
            if i.locked {
                regular += (i.end.min(span_end) - i.start.max(span_start)).max(0) - in_ot;
            } else {
                regular += overlap(i.start, i.end, &w.regular);
            }
            ot += in_ot;
            if i.end <= start {
                before = Some(i);
            } else if i.start >= end && after.is_none() {
                after = Some(i);
            }
        }
        for &(a, b, _) in segs {
            regular += overlap(a, b, &w.regular);
            ot += overlap(a, b, &w.ot);
        }
        if regular > w.regular_cap || ot > w.ot_cap {
            return false;
        }
        if let Some(pv) = before {
            match self.travel_minutes(pv.line, pv.known, line) {
                None => return false,
                Some(minutes) if minutes > 0 && !Self::has_slot(w, pv.end, start, minutes) => return false,
                _ => {}
            }
        }
        if let Some(nx) = after {
            if !nx.locked {
                if let Some(next_line) = nx.line {
                    match self.travel_minutes(Some(line), true, next_line) {
                        None => return false,
                        Some(minutes) if minutes > 0 && !Self::has_slot(w, end, nx.start, minutes) => return false,
                        _ => {}
                    }
                }
            }
        }
        true
    }

    /// A crew of `need` can only come from members who could be booked alone and are qualified, and it may add at most
    /// `cap - active` heads not already active in the pool (`cap_problem_inner`). When even that many people do not exist,
    /// the full crew attempt is certain to fail and is skipped.
    #[allow(clippy::too_many_arguments)]
    fn enough_free(&self, s: u32, q: u32, need: usize, bundle: usize, groups: &[Vec<Arc<str>>], window: Option<(i64, i64)>, line: i64,
                   segs: &[(i64, i64, bool)]) -> bool {
        if need == 0 {
            return true;
        }
        let (start, end) = (segs[0].0, segs[segs.len() - 1].1);
        let Some(members) = self.data().members.get(&q) else { return false };
        let counted = self.data().counted[WORK as usize];
        let room = if counted {
            let active = self.active.get(&q).map_or(0, |m| m.values().filter(|&&n| n > 0).count()) as i64;
            (self.data().pool_caps.get(&q).copied().unwrap_or(0) - active).max(0) as usize
        } else {
            usize::MAX
        };
        let (mut active_free, mut new_free) = (0usize, 0usize);
        for &m in members {
            if !self.could_book(m, s, q, line, segs) || !self.shared.qualifies(m, bundle, groups, window, start, end) {
                continue;
            }
            let is_active = counted && self.active.get(&q).is_some_and(|a| a.get(&m).is_some_and(|&n| n > 0));
            if is_active {
                active_free += 1;
            } else {
                new_free += 1;
            }
            if active_free + new_free.min(room) >= need {
                return true;
            }
        }
        false
    }

    /// `greedy._candidate_starts`: the first valid start, the no-start cutoff and every pool member's duty end, with and
    /// without a transfer, inside [first, cutoff], sorted and unique. Every opening is kept.
    fn candidates(&self, s: u32, q: u32, first: i64, cutoff: i64, travel: i64) -> Vec<i64> {
        let mut points: Vec<i64> = vec![first, cutoff];
        if let Some(members) = self.data().members.get(&q) {
            for &m in members {
                for i in self.items_of(m, s) {
                    if i.kind != TRAVEL {
                        points.push(i.end);
                        points.push(i.end + travel);
                    }
                }
            }
        }
        points.retain(|&t| first <= t && t <= cutoff);
        points.sort_unstable();
        points.dedup();
        points
    }

    /// `greedy.Decoder._book` for one start: the assignment id, the crew choice and, in one transaction, the booking and
    /// the production crew's WAIT duty through an in-process inspection.
    #[allow(clippy::too_many_arguments)]
    fn book_attempt(
        &mut self,
        phase_id: &str,
        purpose: &str,
        line: i64,
        team: &str,
        s: u32,
        q: u32,
        count: i64,
        bundle: usize,
        groups: &[Vec<Arc<str>>],
        window: Option<(i64, i64)>,
        segs: &[(i64, i64, bool)],
        tools: &[Arc<str>],
        wait: &Option<(Vec<u32>, u32, u32, i64)>,
    ) -> Result<(Vec<u32>, String), String> {
        let aid = assignment_id(phase_id, segs[0].0, purpose);
        let chosen = self.choose_inner(&aid, line, team, s, q, count, bundle, groups, window, segs, WORK, tools).map_err(|f| f.reason)?;
        let crew = chosen.crew.clone();
        self.begin();
        self.commit_planned(chosen.planned);
        if let Some((wcrew, ws, wq, wend)) = wait {
            let waid = format!("{aid}:WAIT");
            let wsegs = [(*wend, segs[segs.len() - 1].1, false)];
            match self.plan_inner(&waid, wcrew, line, *ws, *wq, &wsegs, WAIT, &[], None) {
                Ok(planned) => self.commit_planned(planned),
                Err(e) => {
                    self.rollback();
                    return Err(format!("WAIT_DUTY_INFEASIBLE: {}: {}", e.code, e.detail));
                }
            }
        }
        self.end();
        Ok((crew, aid))
    }
}

struct Chosen {
    crew: Vec<u32>,
    planned: Planned,
    tried: usize,
    qualified: usize,
    keyed: Vec<(PrefKey, u32)>,
}

struct ChooseFail {
    reason: String,
    tried: usize,
    qualified: usize,
}

type Placement = (i64, i64, Vec<(i64, i64, bool)>, String, String, Vec<String>, String);
type SearchTuple = (Option<Placement>, String, u64, u64);

#[pymethods]
impl Ledger {
    // -------------------------------------------------------------- queries

    fn shift_items(&mut self, person: &str, shift: &str) -> Vec<ItemTuple> {
        let (p, s) = (self.persons.intern(person), self.shifts.intern(shift));
        self.items_of(p, s).iter().map(|i| self.item_tuple(i)).collect()
    }

    fn all_items(&self) -> Vec<ItemTuple> {
        let mut all: Vec<&Item> = self.shift_items.values().flat_map(|v| v.iter()).collect();
        all.sort_by(|a, b| (self.pname(a.person), a.start, a.end, &*a.id).cmp(&(self.pname(b.person), b.start, b.end, &*b.id)));
        all.into_iter().map(|i| self.item_tuple(i)).collect()
    }

    fn items(&mut self, person: &str) -> Vec<ItemTuple> {
        let p = self.persons.intern(person);
        let mut out: Vec<&Item> = self.shift_items.iter().filter(|((q, _), _)| *q == p).flat_map(|(_, v)| v.iter()).collect();
        out.sort_by(|a, b| cmp_item(a, b));
        out.into_iter().map(|i| self.item_tuple(i)).collect()
    }

    fn bookings(&self) -> Vec<BookingTuple> {
        self.bookings.values().map(|b| self.booking_tuple(b)).collect()
    }

    fn active(&mut self, pool: &str) -> Vec<String> {
        let id = self.pools.intern(pool);
        self.active_set(id).into_iter().map(|p| self.pname(p).to_string()).collect()
    }

    fn pool_cap(&mut self, pool: &str) -> Option<i64> {
        let id = self.pools.intern(pool);
        self.data().pool_caps.get(&id).copied()
    }

    fn duty_minutes(&mut self, person: &str, shift: &str) -> (i64, i64) {
        let (p, s) = (self.persons.intern(person), self.shifts.intern(shift));
        match self.data().windows.get(&(p, s)) {
            Some(w) => Self::budget(w, self.items_of(p, s)),
            None => (0, 0),
        }
    }

    fn last_located(&mut self, person: &str, shift: &str, before: i64) -> Option<ItemTuple> {
        let (p, s) = (self.persons.intern(person), self.shifts.intern(shift));
        let last = self.items_of(p, s).iter().filter(|i| i.kind != TRAVEL && i.end <= before).last()?;
        Some(self.item_tuple(last))
    }

    fn free_points(&mut self, person: &str, shift: &str) -> Vec<i64> {
        let (p, s) = (self.persons.intern(person), self.shifts.intern(shift));
        let mut ends: Vec<i64> = self.items_of(p, s).iter().filter(|i| i.kind != TRAVEL).map(|i| i.end).collect();
        ends.sort_unstable();
        ends.dedup();
        ends
    }

    /// `greedy._candidate_starts`: the floor, the no-start cutoff and every pool member's duty end (plus a transfer).
    fn candidate_starts(&mut self, pool: &str, shift: &str, first: i64, cutoff: i64, travel: i64) -> Vec<i64> {
        let (pid, s) = (self.pools.intern(pool), self.shifts.intern(shift));
        self.candidates(s, pid, first, cutoff, travel)
    }

    fn problems(&self) -> Vec<String> {
        self.problems.clone()
    }

    // -------------------------------------------------------------- carry-in

    #[pyo3(signature = (item_id, person, start, end, kind, line, shift, pool))]
    #[allow(clippy::too_many_arguments)]
    fn add_locked(&mut self, item_id: &str, person: &str, start: i64, end: i64, kind: &str, line: Option<i64>, shift: &str, pool: Option<&str>) -> PyResult<ErrTuple> {
        let k = kind_of(kind)?;
        let (p, s) = (self.persons.intern(person), self.shifts.intern(shift));
        let pool_id = pool.map(|q| self.pools.intern(q));
        let item = Item {
            id: Arc::from(item_id),
            person: p,
            start,
            end,
            kind: k,
            line,
            origin: None,
            known: line.is_some(),
            shift: s,
            pool: pool_id,
            aid: None,
            locked: true,
        };
        if let Some(e) = self.check_cross_shift(p, s, &[(start, end)]) {
            return Ok(Some((e.code, e.detail)));
        }
        let mut items: Vec<Item> = self.items_of(p, s).to_vec();
        items.push(item);
        items.sort_by(cmp_item);
        if let Some(w) = self.data().windows.get(&(p, s)).cloned() {
            match self.derive(p, s, &w, items.clone()) {
                Ok(derived) => {
                    items = derived;
                    if let Some(e) = self.check_budget(p, s, &w, &items) {
                        self.problems.push(format!("{}: {}: {}", item_id, e.code, e.detail));
                    }
                }
                Err(e) => self.problems.push(format!("{}: {}: {}", item_id, e.code, e.detail)),
            }
        }
        self.replace(p, s, Arc::new(items));
        if let Some(q) = pool_id {
            if self.data().counted[k as usize] {
                self.bump(q, p, 1);
            }
        }
        Ok(None)
    }

    // -------------------------------------------------------------- booking

    #[pyo3(signature = (aid, crew, line, shift, pool, segments, kind, tools))]
    #[allow(clippy::too_many_arguments)]
    fn plan(&mut self, aid: &str, crew: Vec<String>, line: i64, shift: &str, pool: &str, segments: Vec<(i64, i64, bool)>, kind: &str, tools: Vec<String>) -> PyResult<(Option<u64>, ErrTuple)> {
        let k = kind_of(kind)?;
        let crew: Vec<u32> = crew.iter().map(|p| self.persons.intern(p)).collect();
        let (s, q) = (self.shifts.intern(shift), self.pools.intern(pool));
        let tools = Self::intern_tools(tools);
        match self.plan_inner(aid, &crew, line, s, q, &segments, k, &tools, None) {
            Ok(planned) => Ok((Some(self.store(planned)), None)),
            Err(e) => Ok((None, Some((e.code, e.detail)))),
        }
    }

    fn commit(&mut self, handle: u64) -> PyResult<()> {
        let planned = self.pending.remove(&handle).ok_or_else(|| PyValueError::new_err(format!("unknown or committed booking handle {handle}")))?;
        self.commit_planned(planned);
        Ok(())
    }

    fn person_conflict(&mut self, person: &str, shift: &str, pool: &str, segments: Vec<(i64, i64, bool)>) -> ErrTuple {
        let (p, s, q) = (self.persons.intern(person), self.shifts.intern(shift), self.pools.intern(pool));
        self.person_conflict_inner(p, s, q, &segments).map(|e| (e.code, e.detail))
    }

    fn cap_problem(&mut self, pool: &str, crew: Vec<String>, kind: &str) -> PyResult<ErrTuple> {
        let k = kind_of(kind)?;
        let q = self.pools.intern(pool);
        let crew: Vec<u32> = crew.iter().map(|p| self.persons.intern(p)).collect();
        Ok(self.cap_problem_inner(q, &crew, k, None).map(|e| (e.code, e.detail)))
    }

    fn remove(&mut self, aid: &str) -> (Option<BookingTuple>, ErrTuple) {
        let Some(booking) = self.bookings.remove(aid) else {
            return (None, Some(("ASSIGNMENT_UNKNOWN".to_string(), aid.to_string())));
        };
        self.log(Undo::Booking(booking.aid.clone(), Some(booking.clone())));
        for &person in &booking.crew {
            let w = self.data().windows[&(person, booking.shift)].clone();
            let items: Vec<Item> = self
                .items_of(person, booking.shift)
                .iter()
                .filter(|i| i.kind != TRAVEL && i.aid.as_deref() != Some(&*booking.aid))
                .cloned()
                .collect();
            let items = match self.derive(person, booking.shift, &w, items) {
                Ok(v) => v,
                Err(e) => return (None, Some((e.code, e.detail))),
            };
            if let Some(e) = self.check_budget(person, booking.shift, &w, &items) {
                return (None, Some((e.code, e.detail)));
            }
            self.replace(person, booking.shift, Arc::new(items));
        }
        if self.data().counted[booking.kind as usize] && self.active.contains_key(&booking.pool) {
            for &person in &booking.crew {
                self.bump(booking.pool, person, -1);
            }
        }
        let tools: Vec<Arc<str>> = self.tool_res.keys().cloned().collect();
        for tool in tools {
            let old = self.tool_res[&tool].clone();
            if old.iter().any(|r| &*r.2 == aid) {
                self.tool_res.insert(tool.clone(), old.iter().filter(|r| &*r.2 != aid).cloned().collect());
                self.log(Undo::Tools(tool, old));
            }
        }
        (Some(self.booking_tuple(&booking)), None)
    }

    // -------------------------------------------------------------- transactions

    fn begin(&mut self) {
        self.marks.push(self.undo.len());
    }

    fn end(&mut self) {
        self.marks.pop();
        if self.marks.is_empty() {
            self.undo.clear();
        }
    }

    fn rollback(&mut self) {
        let mark = self.marks.pop().unwrap_or(0);
        while self.undo.len() > mark {
            match self.undo.pop().unwrap() {
                Undo::Shift(key, old) => {
                    match old {
                        Some(v) => self.shift_items.insert(key, v),
                        None => self.shift_items.remove(&key),
                    };
                }
                Undo::Rows(person, old) => {
                    match old {
                        Some(v) => self.rows.insert(person, v),
                        None => self.rows.remove(&person),
                    };
                }
                Undo::Booking(aid, old) => {
                    match old {
                        Some(v) => self.bookings.insert(aid, v),
                        None => self.bookings.remove(&aid),
                    };
                }
                Undo::Active(pool, person, old) => {
                    let counts = self.active.entry(pool).or_default();
                    match old {
                        Some(n) => counts.insert(person, n),
                        None => counts.remove(&person),
                    };
                }
                Undo::Tools(tool, old) => {
                    self.tool_res.insert(tool, old);
                }
            }
        }
        if self.marks.is_empty() {
            self.undo.clear();
        }
    }

    // -------------------------------------------------------------- crew selection (crew_selection.py)

    /// `choose_crew` without an exact crew: qualification, per-person screening and full planning, preference order,
    /// cap-driven reuse and bounded combinations, all exactly as the Python reference.
    #[pyo3(signature = (aid, line, team, shift, pool, count, bundle, window, segments, kind, tools))]
    #[allow(clippy::too_many_arguments)]
    fn choose_crew(
        &mut self,
        aid: &str,
        line: i64,
        team: &str,
        shift: &str,
        pool: &str,
        count: i64,
        bundle: usize,
        window: Option<(i64, i64)>,
        segments: Vec<(i64, i64, bool)>,
        kind: &str,
        tools: Vec<String>,
    ) -> PyResult<ChooseTuple> {
        let k = kind_of(kind)?;
        if count <= 0 {
            return Ok((None, None, 0, 0, Vec::new(), "CREW_DEMAND_UNAVAILABLE: no sourced positive crew demand (never clamped to 1)".to_string()));
        }
        let (s, q) = (self.shifts.intern(shift), self.pools.intern(pool));
        let tools = Self::intern_tools(tools);
        let groups = self.bundle_groups(bundle)?;
        match self.choose_inner(aid, line, team, s, q, count, bundle, &groups, window, &segments, k, &tools) {
            Ok(c) => {
                let names = c.crew.iter().map(|&p| self.pname(p).to_string()).collect();
                let preference: Vec<(String, PrefKey)> = c.keyed.iter().map(|(key, p)| (self.pname(*p).to_string(), key.clone())).collect();
                let (tried, qualified) = (c.tried, c.qualified);
                let handle = self.store(c.planned);
                Ok((Some(names), Some(handle), tried, qualified, preference, "SELECTED".to_string()))
            }
            Err(f) => Ok((None, None, f.tried, f.qualified, Vec::new(), f.reason)),
        }
    }

    /// The decoder's whole time-slot search for one phase (`greedy.Decoder._search` and `_book`): every eligible shift of
    /// the line from the floor on, every candidate start, regular time before overtime, the crew choice and the booking
    /// (with the WAIT duty of `wait`, when given). Returns (placement, reason, crew attempts, starts skipped).
    ///
    /// A start where fewer qualified people are free than the crew needs is skipped: the crew attempt there is certain to
    /// fail. When the search fails and the last failure the reference would report came from a skipped start, that
    /// attempt is made in full at the end to recover its exact reason; the ledger has not changed in between.
    #[pyo3(signature = (phase_id, line, team, count, bundle, work_minutes, floor, end_floor, tools, wait))]
    #[allow(clippy::too_many_arguments)]
    fn search(
        &mut self,
        phase_id: &str,
        line: i64,
        team: &str,
        count: i64,
        bundle: usize,
        work_minutes: i64,
        floor: i64,
        end_floor: i64,
        tools: Vec<String>,
        wait: Option<(Vec<String>, String, String, i64)>,
    ) -> PyResult<SearchTuple> {
        let cal = self.shared.calendar.lock().unwrap().clone().ok_or_else(|| PyValueError::new_err("calendar not registered"))?;
        let groups = self.bundle_groups(bundle)?;
        let tools = Self::intern_tools(tools);
        let wait = wait.map(|(crew, shift, pool, end)| {
            let ids: Vec<u32> = crew.iter().map(|p| self.persons.intern(p)).collect();
            (ids, self.shifts.intern(&shift), self.pools.intern(&pool), end)
        });
        let travel = self.data().travel_changed;
        let need = count.max(0) as usize;
        let mut last_reason = String::from("NO_ELIGIBLE_SHIFT: no eligible shift within admitted calendar bounds after the floor");
        let mut owed: Option<(u32, u32, Option<(i64, i64)>, Vec<(i64, i64, bool)>)> = None;
        let (mut attempts, mut skipped) = (0u64, 0u64);
        let none: Vec<Arc<ShiftRec>> = Vec::new();
        for shift in cal.lines.get(&line).unwrap_or(&none) {
            if shift.end <= floor || shift.cutoff_instant < floor {
                continue;
            }
            let Some(pool_name) = shift.pools.get(team) else {
                last_reason = format!("POOL_MISSING: no {} pool for {}", team, shift.label);
                owed = None;
                continue;
            };
            let first = match shift.next_open(floor.max(shift.start)) {
                Some(t) if shift.valid_start(t) => t,
                _ => continue,
            };
            let s = self.shifts.intern(&shift.id);
            let q = self.pools.intern(pool_name);
            let window = Some((shift.start, shift.end.max(shift.ot_window.map_or(shift.end, |w| w.1))));
            for t in self.candidates(s, q, first, shift.cutoff_instant, travel) {
                let Some(t2) = shift.next_open(t) else { continue };
                if !shift.valid_start(t2) {
                    continue;
                }
                for ot_allowed in [0, cal.ot_minutes] {
                    let Some((end, segs)) = shift.advance(t2, work_minutes, ot_allowed) else { continue };
                    if end < end_floor {
                        break;
                    }
                    if !self.enough_free(s, q, need, bundle, &groups, window, line, &segs) {
                        skipped += 1;
                        owed = Some((s, q, window, segs));
                        break;
                    }
                    attempts += 1;
                    match self.book_attempt(phase_id, &cal.purpose, line, team, s, q, count, bundle, &groups, window, &segs, &tools, &wait) {
                        Ok((crew, aid)) => {
                            let names = crew.iter().map(|&p| self.pname(p).to_string()).collect();
                            let placed = (segs[0].0, end, segs, shift.id.to_string(), pool_name.to_string(), names, aid);
                            return Ok((Some(placed), "SELECTED".to_string(), attempts, skipped));
                        }
                        Err(reason) => {
                            last_reason = reason;
                            owed = None;
                        }
                    }
                    break;
                }
            }
        }
        if let Some((s, q, window, segs)) = owed {
            let aid = assignment_id(phase_id, segs[0].0, &cal.purpose);
            last_reason = match self.choose_inner(&aid, line, team, s, q, count, bundle, &groups, window, &segs, WORK, &tools) {
                Err(f) => f.reason,
                Ok(_) => return Err(PyValueError::new_err(format!("{phase_id}: a skipped start at {} was feasible", segs[0].0))),
            };
        }
        Ok((None, format!("NO_FEASIBLE_PLACEMENT: {last_reason}"), attempts, skipped))
    }
}

#[pymodule]
fn focus_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Context>()?;
    m.add_class::<Ledger>()?;
    m.add("PARITY_REFERENCE", "focus/engine/ledger.py, focus/engine/crew_selection.py, focus/eligibility.py")?;
    Ok(())
}
