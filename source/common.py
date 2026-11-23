"""Utilidades comunes de la práctica 2 (TCVD, UOC).

Contiene el cálculo de la ventana de tiempo, la gestión de results.json y un
cliente HTTP que respeta las políticas de la API de Wikimedia Commons:
User-Agent identificativo, peticiones en serie (para cumplir con las políticas de la API, especificada en la documentación), pausa aleatoria entre
peticiones, reintentos con Retry-After y registro de cada petición en
dataset/requests.csv.
"""
from __future__ import annotations

import csv
import json
import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("tcvd")

# --- Rutas del repositorio ---------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "dataset"
DOWNLOADS_DIR = ROOT / "downloads"
RESULTS_PATH = ROOT / "results.json"
REQUESTS_PATH = DATASET_DIR / "requests.csv"

# --- Parámetros de la API y de la política de uso ------------------------------
API_URL = "https://commons.wikimedia.org/w/api.php"
CLIENT_NAME = "TCVD-PR2-UOC/1.0"
CONTACT = "practica universitaria UOC; aroldan@uoc.edu"
PAUSE_RANGE = (2.0, 5.0)   # segundos entre peticiones (aleatorio)
DEFAULT_RETRY_WAIT = 60    # segundos si no hay cabecera Retry-After
MAX_RETRIES = 5
TIMEOUT = (10, 120)        # (conexión, lectura) en segundos

# Cabecera de dataset/requests.csv (anexo técnico, apartado 1).
REQUESTS_HEADER = [
    "timestamp", "section", "method", "url",
    "http_status", "bytes", "retry", "duration_ms",
]


class ApiError(Exception):
    """Error devuelto por la API (en el cuerpo de un 200) o código HTTP no reintentable."""


def build_user_agent() -> str:
    """Aporta la descripción del User-Agent."""
    return f"{CLIENT_NAME} ({CONTACT}) python-requests/{requests.__version__}"


# --- Ventana de tiempo -----------------------------------------------------------
def compute_window(seed: int) -> dict:
    """Deriva la ventana de tiempo a partir de la semilla (apartado 1).
    day   = 2023-01-01 + tres últimas cifras de la semilla (días)
    shift = cuarta cifra desde la derecha: par -> 0 (mañana), impar -> 1 (tarde)
    """
    day = date(2023, 1, 1) + timedelta(days=seed % 1000)
    shift = ((seed // 1000) % 10) % 2
    start_time, end_time = ("00:00:00", "11:59:59") if shift == 0 else ("12:00:00", "23:59:59")
    return {
        "seed": seed,
        "day": day.isoformat(),
        "shift": shift,
        "start": f"{day.isoformat()}T{start_time}Z",
        "end": f"{day.isoformat()}T{end_time}Z",
    }


# --- Gestion y uso de results.json ----------------------------------------------------------------------
def load_results(path: Path = RESULTS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_results(results: dict, path: Path = RESULTS_PATH) -> None:
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def set_block(name: str, block: dict, path: Path = RESULTS_PATH) -> None:
    """Escribe un bloque de results.json conservando los demás."""
    results = load_results(path)
    results[name] = block
    save_results(results, path)


# --- requests.csv ---------------------------------------------------------------------------
def init_requests_csv(path: Path = REQUESTS_PATH) -> None:
    """Crea requests.csv con su cabecera si no existe (nunca borra el contenido)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, lineterminator="\n").writerow(REQUESTS_HEADER)


def append_request_row(row: list, path: Path = REQUESTS_PATH) -> None:
    init_requests_csv(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerow(row)


def _retry_after(headers) -> int | None:
    """Segundos indicados por Retry-After (solo formato numérico)."""
    value = headers.get("Retry-After") if headers else None
    return int(value) if value and value.strip().isdigit() else None


# --- Cliente HTTP ------------------------------------------------------------------------------
class ApiClient:
    """Cliente en serie, con pausa aleatoria, reintentos y registro de peticiones."""

    def __init__(self, section: int, pause=PAUSE_RANGE, rng_seed=None, maxlag=None,
                 use_cache: bool = False, requests_path: Path = REQUESTS_PATH,
                 api_url: str = API_URL, sleep=time.sleep):
        self.section = section
        self.pause = pause
        self.rng = random.Random(rng_seed)   # semilla fija: pausas reproducibles
        self.maxlag = maxlag
        self.requests_path = requests_path
        self.api_url = api_url
        self.sleep = sleep
        self.n_requests = 0          # peticiones enviadas (incluye fallos y reintentos)
        self.bytes_downloaded = 0    # solo descargas de ficheros (apartado 4)
        self._first = True
        if use_cache:                # solo durante el desarrollo (requests-cache)
            import requests_cache
            self.session = requests_cache.CachedSession(str(ROOT / "http_cache"), backend="sqlite")
        else:
            self.session = requests.Session()
        self.session.headers["User-Agent"] = build_user_agent()

    # -- API pública --------------------------------------------------------------------------
    def api_get(self, params: dict) -> dict:
        """GET a la Action API; devuelve el JSON ya comprobado (error y avisos)."""
        params = dict(params)
        if self.maxlag is not None:
            params.setdefault("maxlag", self.maxlag)
        return self._fetch(self.api_url, params=params)

    def download(self, url: str, dest: Path) -> Path:
        """Descarga un fichero (en serie y con el mismo ritmo que las consultas)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        return self._fetch(url, dest=dest)

    # -- Internos -------------------------------------------------------------------------------
    def _pause(self) -> None:
        if self._first:
            self._first = False
            return
        self.sleep(self.rng.uniform(*self.pause))

    def _attempt(self, url, params, dest, retry):
        """Envía un GET, lo registra y devuelve (status, headers, body, error)."""
        sent_at = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        status, size, headers, body, error, from_cache = 0, 0, {}, None, None, False
        try:
            resp = self.session.get(url, params=params, stream=dest is not None, timeout=TIMEOUT)
            final_url, status, headers = resp.url, resp.status_code, resp.headers
            from_cache = getattr(resp, "from_cache", False)
            if dest is not None and status == 200:
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
                        size += len(chunk)
            else:
                body = resp.content          # ya descomprimido
                size = len(body)
        except requests.RequestException as exc:
            error = exc
            final_url = requests.Request("GET", url, params=params).prepare().url
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if not from_cache:
            self.n_requests += 1
            append_request_row(
                [sent_at.strftime("%Y-%m-%dT%H:%M:%SZ"), self.section, "GET", final_url,
                 status, size, retry, duration_ms],
                self.requests_path,
            )
            if dest is not None and status == 200:
                self.bytes_downloaded += size
        return status, headers, body, error

    def _fetch(self, url, params=None, dest=None):
        """GET con reintentos: 429, 5xx, fallos de red y error maxlag en un 200."""
        wait = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt == 0:
                self._pause()
            else:
                log.warning("Reintento %d tras esperar %s s", attempt, wait)
                self.sleep(wait)
            status, headers, body, error = self._attempt(url, params, dest, retry=int(attempt > 0))

            retry_after = _retry_after(headers)
            if error is not None:
                reason = f"error de red: {error}"
            elif status == 429 or status >= 500:
                reason = f"HTTP {status}"
            elif status == 200 and dest is not None:
                return dest
            elif status == 200:
                try:
                    data = json.loads(body)
                except ValueError as exc:
                    raise ApiError(f"Respuesta 200 que no es JSON: {exc}") from exc
                err = data.get("error")
                if not err:
                    if data.get("warnings"):
                        log.warning("Avisos de la API: %s", data["warnings"])
                    return data
                if err.get("code") != "maxlag":
                    raise ApiError(err)      # error de la API no reintentable
                reason = "maxlag"
            else:
                raise ApiError(f"HTTP {status} en {url}")

            if attempt == MAX_RETRIES:
                raise ApiError(f"Agotados {MAX_RETRIES} reintentos ({reason})")
            # Primera espera: Retry-After (o 60 s); después se duplica.
            wait = (retry_after or DEFAULT_RETRY_WAIT) if wait is None else wait * 2
