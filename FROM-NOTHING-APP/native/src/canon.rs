//! Canonical JSON (`focus/codec.py`: `canonical_dumps`, `content_hash`, `streamed_content_hash`, and
//! `focus/domain.py`: `digest_id`) written straight from Python values, without building the JSON-native tree.
//!
//! The bytes are exactly those of `json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"),
//! ensure_ascii=False, allow_nan=False).encode()`. Each type follows the rule `codec._encoder_for` picks for it, which
//! Python reports once per type (`codec._native_kind`). Decimal and Fraction values go through `to_jsonable` and
//! datetimes through `format_utc`. Anything else unusual, and every error, raises here, and the Python wrapper then runs
//! the Python path, which returns the result or raises the exact error.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple, PyType};
use sha2::{Digest, Sha256};
use rustc_hash::{FxHashMap as HashMap, FxHashSet as HashSet};
use std::fmt::Write as _;
use std::sync::{Arc, Mutex};

const MAX_DEPTH: usize = 400;

#[derive(Clone)]
enum Kind {
    Null,
    Str,
    Bool,
    Int,
    Float,
    Enum,
    DateTime,
    Date,
    Convert,
    Dataclass(Arc<Vec<(Py<PyString>, Vec<u8>)>>),
    Mapping,
    Sequence,
    Set,
    Unsupported,
}

trait Out {
    fn put(&mut self, bytes: &[u8]);
}

impl Out for Vec<u8> {
    fn put(&mut self, bytes: &[u8]) {
        self.extend_from_slice(bytes);
    }
}

struct HashOut {
    buf: Vec<u8>,
    hasher: Sha256,
}

impl Out for HashOut {
    fn put(&mut self, bytes: &[u8]) {
        self.buf.extend_from_slice(bytes);
        if self.buf.len() >= 1 << 16 {
            self.hasher.update(&self.buf);
            self.buf.clear();
        }
    }
}

impl HashOut {
    fn new() -> Self {
        HashOut { buf: Vec::with_capacity(1 << 17), hasher: Sha256::new() }
    }

    fn hex(mut self) -> String {
        self.hasher.update(&self.buf);
        let digest = self.hasher.finalize();
        let mut out = String::with_capacity(64);
        for byte in digest.iter() {
            let _ = write!(out, "{byte:02x}");
        }
        out
    }
}

fn fallback(why: &str) -> PyErr {
    PyValueError::new_err(format!("NATIVE_FALLBACK: {why}"))
}

/// A JSON string as `json.dumps(..., ensure_ascii=False)` writes it.
fn put_str<O: Out>(out: &mut O, s: &str) {
    out.put(b"\"");
    let bytes = s.as_bytes();
    let mut start = 0;
    for (i, &b) in bytes.iter().enumerate() {
        let esc: Option<&[u8]> = match b {
            b'"' => Some(b"\\\""),
            b'\\' => Some(b"\\\\"),
            b'\n' => Some(b"\\n"),
            b'\r' => Some(b"\\r"),
            b'\t' => Some(b"\\t"),
            0x08 => Some(b"\\b"),
            0x0c => Some(b"\\f"),
            _ => None,
        };
        if esc.is_some() || b < 0x20 {
            out.put(&bytes[start..i]);
            match esc {
                Some(e) => out.put(e),
                None => {
                    let mut tmp = String::with_capacity(6);
                    let _ = write!(tmp, "\\u{:04x}", b);
                    out.put(tmp.as_bytes());
                }
            }
            start = i + 1;
        }
    }
    out.put(&bytes[start..]);
    out.put(b"\"");
}

fn str_bytes(s: &str) -> Vec<u8> {
    let mut v = Vec::with_capacity(s.len() + 2);
    put_str(&mut v, s);
    v
}

/// The native canonical encoder. `kind_of(type) -> (code, sorted field names | None)` reports `codec._encoder_for`'s
/// rule for a type; `to_jsonable` and `format_utc` are the codec's own functions.
#[pyclass(module = "focus_native")]
pub struct Canon {
    kind_of: Py<PyAny>,
    to_jsonable: Py<PyAny>,
    format_utc: Py<PyAny>,
    kinds: Mutex<HashMap<usize, (Py<PyType>, Kind)>>,
}

impl Canon {
    fn kind(&self, py: Python<'_>, tp: &Bound<'_, PyType>) -> PyResult<Kind> {
        let key = tp.as_ptr() as usize;
        if let Some((_, k)) = self.kinds.lock().unwrap().get(&key) {
            return Ok(k.clone());
        }
        let answer = self.kind_of.bind(py).call1((tp,))?;
        let (code, names): (u8, Option<Vec<String>>) = answer.extract()?;
        let kind = match code {
            0 => Kind::Null,
            1 => Kind::Str,
            2 => Kind::Bool,
            3 => Kind::Int,
            4 => Kind::Float,
            5 => Kind::Enum,
            6 => Kind::DateTime,
            7 => Kind::Date,
            8 => Kind::Convert,
            9 => Kind::Dataclass(Arc::new(
                names
                    .unwrap_or_default()
                    .into_iter()
                    .map(|n| {
                        let key = str_bytes(&n);
                        (PyString::intern(py, &n).unbind(), key)
                    })
                    .collect(),
            )),
            10 => Kind::Mapping,
            11 => Kind::Sequence,
            12 => Kind::Set,
            _ => Kind::Unsupported,
        };
        self.kinds.lock().unwrap().insert(key, (tp.clone().unbind(), kind.clone()));
        Ok(kind)
    }

    /// Write `v`. Keys in `ex` are left out of every object at any depth, as `decision_digest`'s strip does after
    /// `to_jsonable` (a dataclass is an object whose keys are its field names).
    fn encode<O: Out>(&self, py: Python<'_>, v: &Bound<'_, PyAny>, out: &mut O, depth: usize, ex: Option<&HashSet<String>>) -> PyResult<()> {
        if depth > MAX_DEPTH {
            return Err(fallback("nesting too deep"));
        }
        // exact JSON-native types first, without a kind lookup
        if v.is_none() {
            out.put(b"null");
            return Ok(());
        }
        if let Ok(s) = v.downcast_exact::<PyString>() {
            put_str(out, s.to_str()?);
            return Ok(());
        }
        if let Ok(b) = v.downcast_exact::<PyBool>() {
            out.put(if b.is_true() { b"true" } else { b"false" });
            return Ok(());
        }
        if v.downcast_exact::<PyInt>().is_ok() {
            return self.int(v, out);
        }
        if let Ok(t) = v.downcast_exact::<PyTuple>() {
            return self.seq(py, t.iter(), out, depth, ex);
        }
        if let Ok(l) = v.downcast_exact::<PyList>() {
            return self.seq(py, l.iter(), out, depth, ex);
        }
        if let Ok(d) = v.downcast_exact::<PyDict>() {
            return self.dict(py, d.iter(), out, depth, ex);
        }
        let tp = v.get_type();
        match self.kind(py, &tp)? {
            Kind::Null => out.put(b"null"),
            Kind::Str => put_str(out, v.downcast::<PyString>()?.to_str()?),
            Kind::Bool => out.put(if v.is_truthy()? { b"true" } else { b"false" }),
            Kind::Int => return self.int(v, out),
            Kind::Float => {
                let f: f64 = v.extract()?;
                if !f.is_finite() {
                    return Err(fallback("non-finite float"));
                }
                let text = PyFloat::new(py, f).repr()?;
                out.put(text.to_str()?.as_bytes());
            }
            Kind::Enum => return self.encode(py, &v.getattr("value")?, out, depth + 1, ex),
            Kind::DateTime => {
                let text = self.format_utc.bind(py).call1((v,))?;
                put_str(out, text.downcast::<PyString>()?.to_str()?);
            }
            Kind::Date => {
                let text = v.call_method0("isoformat")?;
                put_str(out, text.downcast::<PyString>()?.to_str()?);
            }
            Kind::Convert => {
                let native = self.to_jsonable.bind(py).call1((v,))?;
                return self.encode(py, &native, out, depth + 1, ex);
            }
            Kind::Dataclass(fields) => {
                out.put(b"{");
                let mut first = true;
                for (name, key) in fields.iter() {
                    let name = name.bind(py);
                    if let Some(ex) = ex {
                        if ex.contains(name.to_str()?) {
                            continue;
                        }
                    }
                    if !first {
                        out.put(b",");
                    }
                    first = false;
                    out.put(key);
                    out.put(b":");
                    self.encode(py, &v.getattr(name)?, out, depth + 1, ex)?;
                }
                out.put(b"}");
            }
            Kind::Mapping => {
                if let Ok(d) = v.downcast::<PyDict>() {
                    return self.dict(py, d.iter(), out, depth, ex);
                }
                let items = v.call_method0("items")?;
                let mut pairs = Vec::new();
                for item in items.try_iter()? {
                    let (k, val): (Bound<'_, PyAny>, Bound<'_, PyAny>) = item?.extract()?;
                    pairs.push((k, val));
                }
                return self.dict(py, pairs.into_iter(), out, depth, ex);
            }
            Kind::Sequence => {
                if let Ok(t) = v.downcast::<PyTuple>() {
                    return self.seq(py, t.iter(), out, depth, ex);
                }
                if let Ok(l) = v.downcast::<PyList>() {
                    return self.seq(py, l.iter(), out, depth, ex);
                }
                return Err(fallback("sequence"));
            }
            Kind::Set => {
                // sorted by each item's full encoding (`_enc_set`), written with the keys left out when `ex` is set
                let mut items: Vec<(Vec<u8>, Vec<u8>)> = Vec::new();
                for item in v.try_iter()? {
                    let item = item?;
                    let mut buf = Vec::new();
                    self.encode(py, &item, &mut buf, depth + 1, None)?;
                    let written = match ex {
                        Some(_) => {
                            let mut stripped = Vec::new();
                            self.encode(py, &item, &mut stripped, depth + 1, ex)?;
                            stripped
                        }
                        None => Vec::new(),
                    };
                    items.push((buf, written));
                }
                items.sort_by(|a, b| a.0.cmp(&b.0));
                out.put(b"[");
                for (i, (full, written)) in items.iter().enumerate() {
                    if i > 0 {
                        out.put(b",");
                    }
                    out.put(if ex.is_some() { written } else { full });
                }
                out.put(b"]");
            }
            Kind::Unsupported => return Err(fallback("unsupported type")),
        }
        Ok(())
    }

    fn int<O: Out>(&self, v: &Bound<'_, PyAny>, out: &mut O) -> PyResult<()> {
        match v.extract::<i64>() {
            Ok(n) => {
                let mut tmp = String::with_capacity(20);
                let _ = write!(tmp, "{n}");
                out.put(tmp.as_bytes());
                Ok(())
            }
            // an exact int's str() is its decimal text, which JSON writes; a subclass may override it
            Err(_) if v.downcast_exact::<PyInt>().is_ok() => {
                out.put(v.str()?.to_str()?.as_bytes());
                Ok(())
            }
            Err(_) => Err(fallback("integer subclass beyond 64 bits")),
        }
    }

    fn seq<'py, O: Out, I: Iterator<Item = Bound<'py, PyAny>>>(
        &self,
        py: Python<'py>,
        items: I,
        out: &mut O,
        depth: usize,
        ex: Option<&HashSet<String>>,
    ) -> PyResult<()> {
        out.put(b"[");
        for (i, item) in items.enumerate() {
            if i > 0 {
                out.put(b",");
            }
            self.encode(py, &item, out, depth + 1, ex)?;
        }
        out.put(b"]");
        Ok(())
    }

    /// `_enc_mapping` then `sort_keys`: string keys as they are, integer keys as decimal text, sorted by code point
    /// (UTF-8 byte order is code point order). Other keys, and keys equal after conversion, take the Python path.
    fn dict<'py, O: Out, I: Iterator<Item = (Bound<'py, PyAny>, Bound<'py, PyAny>)>>(
        &self,
        py: Python<'py>,
        items: I,
        out: &mut O,
        depth: usize,
        ex: Option<&HashSet<String>>,
    ) -> PyResult<()> {
        let mut pairs: Vec<(String, Bound<'py, PyAny>)> = Vec::new();
        for (k, val) in items {
            let key = if let Ok(s) = k.downcast_exact::<PyString>() {
                s.to_str()?.to_owned()
            } else if k.downcast_exact::<PyInt>().is_ok() {
                match k.extract::<i64>() {
                    Ok(n) => n.to_string(),
                    Err(_) => return Err(fallback("integer key beyond 64 bits")),
                }
            } else {
                return Err(fallback("key type"));
            };
            pairs.push((key, val));
        }
        pairs.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
        for w in pairs.windows(2) {
            if w[0].0 == w[1].0 {
                return Err(fallback("duplicate key after conversion"));
            }
        }
        out.put(b"{");
        let mut first = true;
        for (key, val) in pairs.iter() {
            if ex.is_some_and(|ex| ex.contains(key)) {
                continue;
            }
            if !first {
                out.put(b",");
            }
            first = false;
            put_str(out, key);
            out.put(b":");
            self.encode(py, val, out, depth + 1, ex)?;
        }
        out.put(b"}");
        Ok(())
    }
}

#[pymethods]
impl Canon {
    #[new]
    fn new(kind_of: Py<PyAny>, to_jsonable: Py<PyAny>, format_utc: Py<PyAny>) -> Self {
        Canon { kind_of, to_jsonable, format_utc, kinds: Mutex::new(HashMap::default()) }
    }

    /// `canonical_dumps(value)`.
    fn dumps<'py>(&self, py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
        let mut buf: Vec<u8> = Vec::with_capacity(256);
        self.encode(py, value, &mut buf, 0, None)?;
        Ok(PyBytes::new(py, &buf))
    }

    /// `content_hash(value)`: SHA-256 hex of the canonical bytes, streamed (bounded memory).
    fn sha256(&self, py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
        let mut out = HashOut::new();
        self.encode(py, value, &mut out, 0, None)?;
        Ok(out.hex())
    }

    /// `digest_id(prefix, *parts, length=length)`: prefix + the first `length` hex digits of SHA-256 over the canonical
    /// JSON list of `parts`.
    fn digest(&self, py: Python<'_>, prefix: &str, parts: &Bound<'_, PyTuple>, length: isize) -> PyResult<String> {
        if length < 0 {
            return Err(fallback("negative length"));
        }
        let mut buf: Vec<u8> = Vec::with_capacity(128);
        self.seq(py, parts.iter(), &mut buf, 0, None)?;
        let digest = Sha256::digest(&buf);
        let mut out = String::with_capacity(prefix.len() + 64);
        out.push_str(prefix);
        let mut hex = String::with_capacity(64);
        for byte in digest.iter() {
            let _ = write!(hex, "{byte:02x}");
        }
        out.push_str(&hex[..(length as usize).min(64)]);
        Ok(out)
    }

    /// `digest_id(prefix, strip(to_jsonable(value)), length=length)` where strip drops the keys in `excluded` from every
    /// object at any depth (`orchestrator.decision_digest`), hashed while writing.
    fn digest_excluding(&self, py: Python<'_>, prefix: &str, value: &Bound<'_, PyAny>, excluded: HashSet<String>, length: isize) -> PyResult<String> {
        if length < 0 {
            return Err(fallback("negative length"));
        }
        let ex = excluded;
        let mut out = HashOut::new();
        out.put(b"[");
        self.encode(py, value, &mut out, 1, Some(&ex))?;
        out.put(b"]");
        let hex = out.hex();
        Ok(format!("{prefix}{}", &hex[..(length as usize).min(64)]))
    }
}
