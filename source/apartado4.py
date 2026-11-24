"""Apartado 4: metadatos dentro de los ficheros.

1. Selecciona 20 ficheros de la muestra de más de 500 KiB: los 10 de menor tamaño
   con marca de cámara según metadata_exif_api.csv y los 10 de menor tamaño sin
   ella (si un grupo no llega a 10, se completa con el otro; en caso de empate
   manda el orden de sample.csv).
2. Los descarga de la URL de sample.csv, en serie y con el mismo ritmo que el resto
   de peticiones. Si ya existe en downloads/ con el SHA-1 esperado, no se vuelve a pedir.
3. Lee con Pillow todo lo que contienen de las columnas del anexo y lo guarda en
   dataset/metadata_file.csv, junto con el bloque "files" de results.json.

Uso:
    python source/apartado4.py
"""
import csv
import hashlib
import logging
import math
import sys
from pathlib import Path

from PIL import ExifTags, Image

from apartado2 import pseudonymize
from apartado3 import COLUMNS, EXIF_PATH, SAMPLE_PATH, read_csv, write_csv
from common import (DOWNLOADS_DIR, REQUESTS_PATH, RESULTS_PATH, ApiClient, ApiError,
                    DATASET_DIR, load_results, set_block)

SECTION = 4
MIN_SIZE = 500 * 1024      # 500 KiB, se exige "más de"
PER_GROUP = 10
METADATA_FILE_PATH = DATASET_DIR / "metadata_file.csv"

Image.MAX_IMAGE_PIXELS = None   # Pillow rechaza por defecto las imágenes de más de 89 Mpx


def select_files(sample: list, exif_rows: list, min_size: int = MIN_SIZE,
                 per_group: int = PER_GROUP) -> list:
    """Selección de ficheros (anexo, apartado 4). Devuelve filas de sample.csv en su orden."""
    has_make = {r["title"]: bool(r["make"]) for r in exif_rows}
    candidates = [(int(r["size"]), i, r) for i, r in enumerate(sample) if int(r["size"]) > min_size]
    # Menor tamaño primero; a igualdad, el que va antes en sample.csv.
    with_make = sorted((c for c in candidates if has_make.get(c[2]["title"])), key=lambda c: c[:2])
    without = sorted((c for c in candidates if not has_make.get(c[2]["title"])), key=lambda c: c[:2])
    a, b = with_make[:per_group], without[:per_group]
    if len(a) < per_group:                      # faltan con marca: se completa con los otros
        b = without[:2 * per_group - len(a)]
    elif len(b) < per_group:                    # faltan sin marca: se completa con los otros
        a = with_make[:2 * per_group - len(b)]
    return [c[2] for c in sorted(a + b, key=lambda c: c[1])]


# --- Lectura del EXIF con Pillow (anexo técnico) ---------------------------------------------
def valor(v) -> str:
    """Texto de un valor tal como lo devuelve Pillow; vacío si falta o es nan (racional 0/0)."""
    if v is None:
        return ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    try:
        if math.isnan(float(v)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).rstrip("\x00 ")


def grados(dms, ref) -> str:
    """(grados, minutos, segundos) -> grados decimales con el signo de la referencia N/S, E/W."""
    if not dms:
        return ""
    g, m, s = (float(x) for x in dms)
    if any(math.isnan(x) for x in (g, m, s)):
        return ""
    dec = g + m / 60 + s / 3600
    return str(-dec if valor(ref) in ("S", "W") else dec)


def read_file_metadata(path: Path) -> dict:
    """Metadatos que contiene el fichero, con las columnas de los ficheros de metadatos."""
    out = {c: "" for c in COLUMNS[1:]}
    out["size"] = str(path.stat().st_size)
    out["sha1"] = hashlib.sha1(path.read_bytes()).hexdigest()
    try:
        with Image.open(path) as img:
            out["width"], out["height"] = (str(x) for x in img.size)   # dimensiones almacenadas
            exif = img.getexif()
            ifd_exif = exif.get_ifd(ExifTags.IFD.Exif)
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception as exc:        # fichero ilegible: se conservan solo size y sha1
        logging.warning("No se pudo leer %s con Pillow: %s", path.name, exc)
        return out
    out.update({
        "date": valor(ifd_exif.get(0x9003)),                  # DateTimeOriginal
        "latitude": grados(gps.get(2), gps.get(1)),
        "longitude": grados(gps.get(4), gps.get(3)),
        "altitude": valor(gps.get(6)),                        # sin conversión (anexo)
        "author": pseudonymize(valor(exif.get(0x013B))),      # Artist, seudonimizado
        "credit": valor(exif.get(0x8298)),                    # Copyright
        "make": valor(exif.get(0x010F)),
        "model": valor(exif.get(0x0110)),
        "software": valor(exif.get(0x0131)),
        "orientation": valor(exif.get(0x0112)),
    })
    return out


def section_stats(section: int, path: Path = REQUESTS_PATH) -> dict:
    """Peticiones, bytes de las respuestas 200, fallos y suma de duraciones en requests.csv."""
    stats = {"requests": 0, "bytes_downloaded": 0, "failed": 0, "duration_ms": 0}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["section"] != str(section):
                continue
            stats["requests"] += 1
            stats["duration_ms"] += int(r["duration_ms"])
            if r["http_status"] == "200":
                stats["bytes_downloaded"] += int(r["bytes"])
            else:
                stats["failed"] += 1
    return stats


def run(client: ApiClient, sample_path: Path = SAMPLE_PATH, exif_path: Path = EXIF_PATH,
        out_path: Path = METADATA_FILE_PATH, downloads_dir: Path = DOWNLOADS_DIR,
        results_path: Path = RESULTS_PATH, requests_path: Path = REQUESTS_PATH,
        min_size: int = MIN_SIZE) -> dict:
    chosen = select_files(read_csv(sample_path), read_csv(exif_path), min_size)
    rows, failed, sha_mismatch = [], [], []
    for item in chosen:
        dest = downloads_dir / f"{item['sha1']}.jpg"        # nombre seguro y único
        try:
            if not (dest.exists() and hashlib.sha1(dest.read_bytes()).hexdigest() == item["sha1"]):
                client.download(item["url"], dest)
        except ApiError as exc:
            logging.error("Fallo al descargar %s: %s", item["title"], exc)
            failed.append(item["title"])
            continue
        meta = read_file_metadata(dest)
        if meta["sha1"] != item["sha1"]:
            sha_mismatch.append(item["title"])
        rows.append({"title": item["title"], **meta})
    write_csv(out_path, COLUMNS, rows)

    stats = section_stats(SECTION, requests_path)
    block = {"requests": stats["requests"], "n": len(rows),
             "bytes_downloaded": stats["bytes_downloaded"]}
    set_block("files", block, results_path)
    return {"block": block, "stats": stats, "chosen": chosen, "rows": rows,
            "failed": failed, "sha_mismatch": sha_mismatch}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    window = load_results().get("window")
    if not window or not SAMPLE_PATH.exists() or not EXIF_PATH.exists():
        sys.exit("Faltan results.json o los ficheros del apartado 3: ejecuta antes los apartados 1 a 3")

    client = ApiClient(section=SECTION, rng_seed=window["seed"])
    out = run(client)
    print(f"Ficheros leídos: {out['block']['n']} | peticiones: {out['block']['requests']} "
          f"| bytes descargados: {out['block']['bytes_downloaded']}")
    s = out["stats"]
    print(f"Tiempo de las descargas (suma de duration_ms): {s['duration_ms'] / 1000:.1f} s "
          f"| peticiones fallidas: {s['failed']}")
    if out["failed"]:
        print("Sin descargar:", out["failed"])
    if out["sha_mismatch"]:
        print("SHA-1 distinto al del catálogo:", out["sha_mismatch"])
    print("Celdas rellenas por columna:")
    for col in COLUMNS[1:]:
        print(f"  {col:22s} {sum(1 for r in out['rows'] if r.get(col)):3d}")


if __name__ == "__main__":
    main()
