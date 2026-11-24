"""Apartado 2: listado de los ficheros subidos en la ventana (list=allimages).

Lee la ventana de results.json, recorre todas las páginas de list=allimages,
comprueba que la lista esté completa, seudonimiza la autoría y escribe
dataset/population.csv y el bloque "population" de results.json.

Uso:
    python source/apartado2.py            # peticiones reales a Commons
    python source/apartado2.py --cache    # con requests-cache (solo desarrollo)
"""
import argparse
import csv
import hashlib
import html
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from common import (DATASET_DIR, RESULTS_PATH, ApiClient, ApiError,
                    count_requests, load_results, set_block)

SECTION = 2
POPULATION_PATH = DATASET_DIR / "population.csv"
# Columnas de population.csv (anexo técnico): mismo nombre y orden que la API.
HEADER = ["title", "timestamp", "size", "mime", "width", "height", "sha1", "user", "url"]


def pseudonymize(name: str) -> str:
    """Seudónimo de una autoría: 12 primeros hex del SHA-256 del nombre normalizado.

    Normalización (anexo): quitar etiquetas HTML, decodificar entidades HTML y
    recortar espacios de los extremos; UTF-8 y sin sal.
    """
    text = html.unescape(re.sub(r"<[^>]*>", "", name)).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else ""


def to_row(item: dict) -> dict:
    """Convierte un elemento de allimages en una fila de population.csv."""
    # Si el usuario está oculto (userhidden) la API no da "user": celda vacía.
    user = item.get("user", "")
    return {
        "title": item["title"],                    # con prefijo File: y con espacios
        "timestamp": item.get("timestamp", ""),
        "size": int(item.get("size", 0)),
        "mime": item.get("mime", ""),
        "width": int(item.get("width", 0)),        # 0 si el fichero no tiene dimensiones
        "height": int(item.get("height", 0)),
        "sha1": item.get("sha1", ""),
        "user": pseudonymize(user) if user else "",
        "url": item.get("url", ""),
    }


def fetch_all(client: ApiClient, window: dict):
    """Recorre todas las páginas de allimages. Devuelve (elementos, nº de páginas)."""
    params = {
        "action": "query",
        "list": "allimages",
        "aisort": "timestamp",
        "aidir": "ascending",
        "aistart": window["start"],
        "aiend": window["end"],
        "aiprop": "timestamp|user|size|sha1|mime|url",
        "ailimit": "max",
        "format": "json",
    }
    items, pages, cont = [], 0, {}
    while True:
        data = client.api_get({**params, **cont})
        pages += 1
        items += data.get("query", {}).get("allimages", [])
        new_cont = data.get("continue")
        if not new_cont:
            return items, pages            # sin "continue": no quedan más páginas
        if new_cont == cont:
            raise ApiError(f"La paginación no avanza: {new_cont}")
        cont = new_cont
        logging.info("Página %d: %d ficheros acumulados", pages, len(items))


def check_population(rows: list, window: dict) -> dict:
    """Comprobaciones de completitud y de pertenencia a la ventana."""
    titles = [r["title"] for r in rows]
    stamps = [r["timestamp"] for r in rows]
    return {
        "duplicated_titles": len(titles) - len(set(titles)),
        "outside_window": sum(not (window["start"] <= t <= window["end"]) for t in stamps),
        "out_of_order": sum(a > b for a, b in zip(stamps, stamps[1:])),
    }


def dedupe(rows: list) -> list:
    """Un solo registro por título (se conserva el primero, en el orden de la API)."""
    seen, unique = set(), []
    for r in rows:
        if r["title"] not in seen:
            seen.add(r["title"])
            unique.append(r)
    return unique


def write_population(rows: list, path: Path = POPULATION_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(client: ApiClient, window: dict, population_path: Path = POPULATION_PATH,
        results_path: Path = RESULTS_PATH) -> dict:
    items, pages = fetch_all(client, window)
    rows = [to_row(i) for i in items]
    checks = check_population(rows, window)
    rows = dedupe(rows)
    write_population(rows, population_path)

    stamps = [r["timestamp"] for r in rows]
    mime_types = dict(Counter(r["mime"] for r in rows).most_common())
    block = {
        # Se cuenta en requests.csv para que sea correcto aunque se repita el script.
        "requests": count_requests(SECTION),
        "n": len(rows),
        "first_upload": min(stamps) if stamps else "",
        "last_upload": max(stamps) if stamps else "",
        "mime_types": mime_types,
    }
    set_block("population", block, results_path)
    return {"block": block, "checks": checks, "pages": pages}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", action="store_true",
                        help="usar requests-cache (solo durante el desarrollo)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    window = load_results().get("window")
    if not window:
        sys.exit("Falta el bloque 'window' en results.json: ejecuta antes apartado1.py")

    client = ApiClient(section=SECTION, rng_seed=window["seed"], use_cache=args.cache)
    out = run(client, window)
    block, checks = out["block"], out["checks"]

    print(f"Páginas leídas: {out['pages']} | peticiones del apartado: {block['requests']}")
    print(f"Ficheros (n): {block['n']} | primera subida: {block['first_upload']} "
          f"| última: {block['last_upload']}")
    print("Comprobaciones:", checks)
    for mime, count in block["mime_types"].items():
        print(f"  {mime}: {count} ({100 * count / block['n']:.1f} %)")


if __name__ == "__main__":
    main()
