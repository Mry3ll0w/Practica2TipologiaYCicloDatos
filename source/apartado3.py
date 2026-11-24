"""Apartado 3: muestra sistemática y metadatos del catálogo (prop=imageinfo).

1. Toma de dataset/population.csv las imágenes JPEG, las ordena por fecha de
   subida y título y selecciona una de cada k = n // 100 (las 100 primeras).
2. Pide a la API, en lotes de 50 títulos, las dos capas de metadatos: la ficha
   (extmetadata) y el EXIF extraído por el catálogo (commonmetadata).
3. Escribe dataset/sample.csv, dataset/metadata_description.csv,
   dataset/metadata_exif_api.csv y el bloque "sample" de results.json.

Los valores se guardan tal como llegan de la API; solo se seudonimiza la autoría.

Uso:
    python source/apartado3.py            # peticiones reales a Commons
    python source/apartado3.py --cache    # con requests-cache (solo desarrollo)
"""
import argparse
import csv
import json
import logging
import math
import sys
from pathlib import Path

from apartado2 import HEADER, POPULATION_PATH, pseudonymize
from common import (DATASET_DIR, RESULTS_PATH, ApiClient, HttpStatusError,
                    count_requests, load_results, set_block)

SECTION = 3
SAMPLE_SIZE = 100
TITLES_PER_REQUEST = 50    # límite de títulos por consulta (API:Query, cuenta sin apihighlimits)
SAMPLE_PATH = DATASET_DIR / "sample.csv"
DESCRIPTION_PATH = DATASET_DIR / "metadata_description.csv"
EXIF_PATH = DATASET_DIR / "metadata_exif_api.csv"

# Columnas de los tres ficheros de metadatos (anexo técnico, apartado 3).
COLUMNS = ["title", "date", "latitude", "longitude", "altitude", "author", "credit",
           "license", "license_url", "usage_terms", "attribution_required", "copyrighted",
           "make", "model", "software", "orientation", "width", "height", "size", "sha1"]

IMAGEINFO_PARAMS = {
    "action": "query",
    "prop": "imageinfo",
    "iiprop": "extmetadata|commonmetadata|size|sha1",
    "iiextmetadatalanguage": "en",   # textos de la ficha independientes del idioma
    "format": "json",
}


def read_csv(path: Path) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def select_sample(rows: list, size: int = SAMPLE_SIZE):
    """Muestra sistemática de imágenes JPEG. Devuelve (muestra, n_jpeg, k)."""
    jpeg = sorted((r for r in rows if r["mime"] == "image/jpeg"),
                  key=lambda r: (r["timestamp"], r["title"]))
    n = len(jpeg)
    if n < size:                       # pocas imágenes: se toman todas
        return jpeg, n, 0
    k = n // size
    return jpeg[::k][:size], n, k


def as_text(value) -> str:
    """Valor de la API como texto, sin interpretarlo (estructuras como JSON compacto)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def request_batch(client: ApiClient, params: dict) -> dict:
    """GET; si la URL resulta demasiado larga se repite por POST (se registra como reintento)."""
    try:
        return client.api_get(params)
    except HttpStatusError as exc:
        if exc.status not in (413, 414, 431):
            raise
        logging.warning("URL demasiado larga (HTTP %s): se repite por POST", exc.status)
        return client.api_get(params, post=True, retry=True)


def fetch_imageinfo(client: ApiClient, titles: list) -> dict:
    """Devuelve {título: página} con imageinfo de un lote de títulos."""
    pages, cont = {}, {}
    while True:
        params = {**IMAGEINFO_PARAMS, "titles": "|".join(titles), **cont}
        data = request_batch(client, params)
        for page in data.get("query", {}).get("pages", {}).values():
            old = pages.get(page["title"])
            if old is None or ("imageinfo" not in old and "imageinfo" in page):
                pages[page["title"]] = page
        cont = data.get("continue")
        if not cont:
            return pages


def description_row(title: str, info: dict) -> dict:
    """Fila de la ficha (extmetadata) y de los datos básicos del catálogo."""
    ext = info.get("extmetadata") or {}

    def em(key):
        item = ext.get(key)
        return as_text(item.get("value")) if isinstance(item, dict) else ""

    return {
        "title": title,
        "date": em("DateTimeOriginal"),
        "latitude": em("GPSLatitude"),
        "longitude": em("GPSLongitude"),
        "altitude": em("GPSAltitude"),
        "author": pseudonymize(em("Artist")),
        "credit": em("Credit"),
        "license": em("LicenseShortName"),
        "license_url": em("LicenseUrl"),
        "usage_terms": em("UsageTerms"),
        "attribution_required": em("AttributionRequired"),
        "copyrighted": em("Copyrighted"),
        # make, model, software y orientation no existen en la ficha: se dejan vacíos.
        "width": as_text(info.get("width")),
        "height": as_text(info.get("height")),
        "size": as_text(info.get("size")),
        "sha1": as_text(info.get("sha1")),
    }


def exif_row(title: str, info: dict) -> dict:
    """Fila del EXIF que el catálogo extrajo al subir el fichero (commonmetadata)."""
    common = {m["name"]: m.get("value") for m in (info.get("commonmetadata") or [])
              if isinstance(m, dict) and "name" in m}

    def cm(key):
        return as_text(common.get(key))

    return {
        "title": title,
        "date": cm("DateTimeOriginal"),
        "latitude": cm("GPSLatitude"),
        "longitude": cm("GPSLongitude"),
        "altitude": cm("GPSAltitude"),
        "author": pseudonymize(cm("Artist")),
        "credit": cm("Copyright"),
        "make": cm("Make"),
        "model": cm("Model"),
        "software": cm("Software"),
        "orientation": cm("Orientation"),
        "width": cm("ImageWidth"),
        "height": cm("ImageLength"),
        # size y sha1 no son EXIF: van en la ficha. La licencia tampoco existe aquí.
    }


def run(client: ApiClient, population_path: Path = POPULATION_PATH,
        sample_path: Path = SAMPLE_PATH, description_path: Path = DESCRIPTION_PATH,
        exif_path: Path = EXIF_PATH, results_path: Path = RESULTS_PATH) -> dict:
    sample, n_jpeg, k = select_sample(read_csv(population_path))
    write_csv(sample_path, HEADER, sample)

    description, exif, missing = [], [], []
    titles = [r["title"] for r in sample]
    for i in range(0, len(titles), TITLES_PER_REQUEST):
        batch = titles[i:i + TITLES_PER_REQUEST]
        pages = fetch_imageinfo(client, batch)
        for title in batch:
            page = pages.get(title)
            info = (page or {}).get("imageinfo", [{}])[0] if page and "imageinfo" in page else None
            if info is None:        # fichero borrado o no devuelto por la API
                missing.append(title)
                info = {}
            description.append(description_row(title, info))
            exif.append(exif_row(title, info))
    write_csv(description_path, COLUMNS, description)
    write_csv(exif_path, COLUMNS, exif)

    block = {
        "requests": count_requests(SECTION),
        "titles_per_request": TITLES_PER_REQUEST,
        "window_cost": math.ceil(n_jpeg / TITLES_PER_REQUEST),
        "sample_cost": math.ceil(len(sample) / TITLES_PER_REQUEST),
        "n": len(sample),
    }
    set_block("sample", block, results_path)
    return {"block": block, "n_jpeg": n_jpeg, "k": k, "missing": missing,
            "description": description, "exif": exif}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", action="store_true",
                        help="usar requests-cache (solo durante el desarrollo)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    window = load_results().get("window")
    if not window or not POPULATION_PATH.exists():
        sys.exit("Faltan results.json o dataset/population.csv: ejecuta antes los apartados 1 y 2")

    client = ApiClient(section=SECTION, rng_seed=window["seed"], use_cache=args.cache)
    out = run(client)
    print(f"Imágenes JPEG (n): {out['n_jpeg']} | k = {out['k']} | muestra: {out['block']['n']}")
    print(f"Peticiones del apartado: {out['block']['requests']} "
          f"| coste de la ventana: {out['block']['window_cost']} "
          f"| coste de la muestra: {out['block']['sample_cost']}")
    if out["missing"]:
        print("Títulos sin imageinfo (borrados o no devueltos):", out["missing"])
    print("Celdas rellenas por columna (descripción / EXIF del catálogo):")
    for col in COLUMNS[1:]:
        d = sum(1 for r in out["description"] if r.get(col))
        e = sum(1 for r in out["exif"] if r.get(col))
        print(f"  {col:22s} {d:3d} / {e:3d}")


if __name__ == "__main__":
    main()
