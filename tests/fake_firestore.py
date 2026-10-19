"""A small in-memory stand-in for google.cloud.firestore.AsyncClient, used by the tests only.

It implements just the calls the app makes (collection/document/get/set/update/create/delete,
where/order_by/limit/start_after/select/stream/count, batch, ArrayUnion/ArrayRemove/SERVER_TIMESTAMP)
and mimics Firestore behaviour that matters: update() fails on missing docs, create() fails on
existing docs, dotted field paths, and order_by()/range filters skip documents missing that field.
"""
import copy
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.firestore_v1.transforms import SERVER_TIMESTAMP, ArrayRemove, ArrayUnion

OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">=": lambda a, b: a >= b,
       "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def _now():
    return datetime.now(timezone.utc)


def _resolve(store_val, value, existing):
    """Apply transform sentinels."""
    if value is SERVER_TIMESTAMP:
        return _now()
    if isinstance(value, ArrayUnion):
        cur = list(existing or [])
        for v in value.values:
            if v not in cur:
                cur.append(v)
        return cur
    if isinstance(value, ArrayRemove):
        return [x for x in (existing or []) if x not in value.values]
    if isinstance(value, dict):
        return {k: _resolve(None, v, (existing or {}).get(k) if isinstance(existing, dict) else None) for k, v in value.items()}
    return copy.deepcopy(value)


def _get_path(d, path):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None, False
        cur = cur[p]
    return cur, True


def _set_path(d, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = _resolve(None, value, d.get(parts[-1]))


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


class Snap:
    def __init__(self, ref, data):
        self.reference, self._d = ref, data
        self.id, self.exists = ref.id, data is not None

    def to_dict(self):
        return copy.deepcopy(self._d) if self._d is not None else None


class DocRef:
    def __init__(self, client, path):
        self._c, self.path, self.id = client, path, path[-1]

    def collection(self, name):
        return CollRef(self._c, self.path + (name,))

    async def get(self):
        return Snap(self, self._c.store.get(self.path))

    async def set(self, data, merge=False):
        new = {k: _resolve(None, v, (self._c.store.get(self.path) or {}).get(k)) for k, v in data.items()}
        if merge and self.path in self._c.store:
            _merge(self._c.store[self.path], new)
        else:
            self._c.store[self.path] = new

    async def create(self, data):
        if self.path in self._c.store:
            raise AlreadyExists("exists")
        self._c.store[self.path] = {k: _resolve(None, v, None) for k, v in data.items()}

    async def update(self, data):
        if self.path not in self._c.store:
            raise NotFound("no document")
        doc = self._c.store[self.path]
        for k, v in data.items():
            _set_path(doc, k, v)

    async def delete(self):
        self._c.store.pop(self.path, None)


class AggResult:
    def __init__(self, v):
        self.value = v


class Agg:
    def __init__(self, q):
        self.q = q

    async def get(self):
        return [[AggResult(len(self.q._run()))]]


class Query:
    def __init__(self, client, path, filters=(), orders=(), lim=None, after=None, fields=None):
        self.c, self.path = client, path
        self.filters, self.orders, self.lim, self.after, self.fields = list(filters), list(orders), lim, after, fields

    def _clone(self, **kw):
        d = dict(client=self.c, path=self.path, filters=self.filters, orders=self.orders, lim=self.lim, after=self.after, fields=self.fields)
        d.update(kw)
        return Query(**d)

    def where(self, *a, filter=None, **kw):
        assert filter is not None, "use where(filter=FieldFilter(...))"
        return self._clone(filters=self.filters + [(filter.field_path, filter.op_string, filter.value)])

    def order_by(self, field, direction="ASCENDING"):
        return self._clone(orders=self.orders + [(field, direction)])

    def limit(self, n):
        return self._clone(lim=n)

    def start_after(self, snap):
        return self._clone(after=snap)

    def select(self, fields):
        return self._clone(fields=fields)

    def count(self):
        return Agg(self)

    def _run(self):
        rows = []
        for p, d in self.c.store.items():
            if len(p) != len(self.path) + 1 or p[:-1] != self.path:
                continue
            ok = True
            for f, op, v in self.filters:
                cur, found = _get_path(d, f)
                if not found or cur is None or not OPS[op](cur, v):
                    ok = False
                    break
            if ok and all(_get_path(d, f)[1] for f, _ in self.orders):
                rows.append((p, d))
        for f, direction in reversed(self.orders):
            rows.sort(key=lambda r: (_get_path(r[1], f)[0], r[0]), reverse=direction == "DESCENDING")
        if self.after is not None and self.orders:
            f, direction = self.orders[0]
            av = _get_path(self.after._d, f)[0]
            rows = [r for r in rows if (_get_path(r[1], f)[0] < av if direction == "DESCENDING" else _get_path(r[1], f)[0] > av)
                    or (_get_path(r[1], f)[0] == av and r[0] > self.after.reference.path)]
        if self.lim is not None:
            rows = rows[: self.lim]
        return rows

    async def stream(self):
        for p, d in self._run():
            yield Snap(DocRef(self.c, p), d)

    async def get(self):
        return [s async for s in self.stream()]


class CollRef(Query):
    def __init__(self, client, path):
        super().__init__(client, path)

    def document(self, doc_id=None):
        import uuid
        return DocRef(self.c, self.path + (doc_id or uuid.uuid4().hex[:20],))

    async def add(self, data):
        ref = self.document()
        await ref.create(data)
        return _now(), ref


class Batch:
    def __init__(self, client):
        self.c, self.ops = client, []

    def set(self, ref, data, merge=False):
        self.ops.append(("set", ref, data))

    def delete(self, ref):
        self.ops.append(("delete", ref, None))

    async def commit(self):
        for op, ref, data in self.ops:
            await (ref.set(data) if op == "set" else ref.delete())
        self.ops = []


class FakeClient:
    project = "fake-project"

    def __init__(self):
        self.store = {}

    def collection(self, name):
        return CollRef(self, (name,))

    def batch(self):
        return Batch(self)

    async def close(self):
        pass
