from __future__ import annotations

from typing import Any, Iterable

import requests

SUPABASE_SDK_AVAILABLE = True
try:  # pragma: no cover - optional dependency
    from supabase import Client as _SupabaseSdkClient, create_client as _sdk_create_client
except ImportError:  # pragma: no cover - fallback to REST
    _SupabaseSdkClient = Any  # type: ignore[misc,assignment]
    SUPABASE_SDK_AVAILABLE = False


class _RestResponse:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class _RestTableBuilder:
    def __init__(self, client: "_RestClient", table: str):
        self._client = client
        self._table = table
        self._action: str | None = None
        self._payload: Any = None
        self._filters: list[tuple[str, tuple[str, Any]]] = []
        self._select = "*"
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None

    def insert(self, payload):
        self._action = "insert"
        self._payload = payload
        return self

    def delete(self):
        self._action = "delete"
        return self

    def select(self, columns: str = "*"):
        self._action = "select"
        self._select = columns or "*"
        return self

    def eq(self, field: str, value: Any):
        self._filters.append((field, ("eq", value)))
        return self

    def in_(self, field: str, values: Iterable[Any]):
        self._filters.append((field, ("in", list(values))))
        return self

    def limit(self, count: int | None):
        self._limit = count
        return self

    def range(self, start: int, end: int):
        self._range = (start, end)
        return self

    def execute(self):
        return self._client._execute_table(
            table=self._table,
            action=self._action,
            payload=self._payload,
            filters=self._filters,
            select=self._select,
            limit=self._limit,
            range_span=self._range,
        )


class _RestRpcBuilder:
    def __init__(self, client: "_RestClient", fn: str, params: dict | None):
        self._client = client
        self._fn = fn
        self._params = params or {}

    def execute(self):
        return self._client._execute_rpc(self._fn, self._params)


class _RestClient:
    def __init__(self, url: str, key: str, *, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout
        self.base = f"{self.url}/rest/v1"
        self.session = requests.Session()
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def table(self, name: str):
        return _RestTableBuilder(self, name)

    def rpc(self, fn: str, params: dict | None):
        return _RestRpcBuilder(self, fn, params)

    def _execute_table(
        self,
        *,
        table: str,
        action: str | None,
        payload: Any,
        filters: list[tuple[str, tuple[str, Any]]],
        select: str,
        limit: int | None,
        range_span: tuple[int, int] | None,
    ):
        url = f"{self.base}/{table}"
        if action == "insert":
            resp = self.session.post(url, headers=self.headers, json=payload, timeout=self.timeout)
        elif action == "delete":
            params = {field: f"eq.{value}" for field, value in filters}
            resp = self.session.delete(url, headers=self.headers, params=params, timeout=self.timeout)
        else:
            params: dict[str, Any] = {}
            headers = dict(self.headers)
            if select:
                params["select"] = select
            if limit is not None:
                params["limit"] = limit
            if range_span:
                headers["Range"] = f"{range_span[0]}-{range_span[1]}"
            for field, (operator, value) in filters:
                if operator == "eq":
                    params[field] = f"eq.{value}"
                elif operator == "in":
                    joined = ",".join(str(v) for v in value)
                    params[field] = f"in.({joined})"
            resp = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
        if resp.ok:
            data = resp.json() if resp.content else None
            return _RestResponse(data=data, error=None)
        return _RestResponse(data=None, error=resp.text)

    def _execute_rpc(self, fn: str, params: dict):
        url = f"{self.base}/rpc/{fn}"
        resp = self.session.post(url, headers=self.headers, json=params, timeout=self.timeout)
        if resp.ok:
            data = resp.json() if resp.content else None
            return _RestResponse(data=data, error=None)
        return _RestResponse(data=None, error=resp.text)


def create_supabase_client(
    url: str,
    key: str,
    *,
    timeout: float | None = None,
):
    cleaned_url = (url or "").strip()
    cleaned_key = (key or "").strip()
    if not cleaned_url or not cleaned_key:
        raise ValueError("Supabase URL and key are required to create a client.")
    client_timeout = timeout or 5.0
    if SUPABASE_SDK_AVAILABLE:
        return _sdk_create_client(cleaned_url, cleaned_key)
    return _RestClient(cleaned_url, cleaned_key, timeout=client_timeout)


Client = _SupabaseSdkClient | _RestClient | Any  # type: ignore[valid-type]

__all__ = [
    "Client",
    "SUPABASE_SDK_AVAILABLE",
    "create_supabase_client",
]
