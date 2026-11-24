# Práctica 2: ¿Cómo extraer los datos mediante APIs?

Tipología y ciclo de vida de los datos (M2.851), UOC.

- **Integrante:** Antonio Roldan Andrade
- **Semilla (número de estudiante elegido):** 1945750

Pregunta de la práctica: ¿coinciden los metadatos que Wikimedia Commons publica
a través de su API con los que contiene el propio fichero?

## Archivos del repositorio

| Ruta | Contenido |
|---|---|
| `README.md` | Este documento |
| `requirements.txt` | Librerías necesarias |
| `results.json` | Resultados de los apartados 1 a 5 |
| `validar.py` | Validador de formato de la plantilla (`python validar.py .`) |
| `source/common.py` | Ventana de tiempo, cliente HTTP (User-Agent, pausa, reintentos) y registro de peticiones |
| `source/apartado1.py` | Apartado 1: ventana de tiempo, `results.json` y `dataset/requests.csv` |
| `source/apartado2.py` | Apartado 2: listado de la ventana con `list=allimages`, seudonimización y `population.csv` |
| `source/apartado3.py` | Apartado 3: muestra sistemática de 100 JPEG y metadatos del catálogo (`prop=imageinfo`) |
| `source/apartado4.py` | Apartado 4: selección y descarga de 20 ficheros y lectura de sus metadatos con Pillow |
| `dataset/requests.csv` | Registro de todas las peticiones HTTP |
| `dataset/population.csv` | Apartado 2: ficheros subidos en la ventana, con la autoría seudonimizada |
| `dataset/sample.csv` | Apartado 3: muestra sistemática de 100 imágenes JPEG |
| `dataset/metadata_description.csv` | Apartado 3: ficha del catálogo (`extmetadata`) |
| `dataset/metadata_exif_api.csv` | Apartado 3: EXIF extraído por el catálogo (`commonmetadata`) |
| `dataset/metadata_file.csv` | Apartado 4: metadatos leídos del propio fichero |
| `downloads/` | Ficheros descargados (no se suben al repositorio) |

## Cómo usar el código

Requiere Python 3.10 o superior.

```bash
python -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Apartado 1

```bash
python source/apartado1.py --seed 1945750
```

| Parámetro | Descripción |
|---|---|
| `--seed` | Semilla: el menor de los números de estudiante (IDP) del grupo (obligatorio) |

Crea o actualiza los bloques `api` y `window` de `results.json` y, si no existe,
`dataset/requests.csv` con su cabecera. No envía peticiones.

### Apartado 2

```bash
python source/apartado2.py            # peticiones reales a Commons
python source/apartado2.py --cache    # con requests-cache (solo desarrollo)
```

| Parámetro | Descripción |
|---|---|
| `--cache` | Usa `requests-cache` (fichero SQLite local, no se sube al repositorio) para no repetir peticiones al probar. Solo se registran en `requests.csv` las respuestas que no vienen de la caché |

Lee la ventana de `results.json` (ejecutar antes el apartado 1), recorre todas las páginas
de `list=allimages`, imprime las comprobaciones de completitud (títulos repetidos, entradas
fuera de la ventana y fechas desordenadas) y escribe `dataset/population.csv` y el bloque
`population` de `results.json`.

### Apartado 3

```bash
python source/apartado3.py            # peticiones reales a Commons
python source/apartado3.py --cache    # con requests-cache (solo desarrollo)
```

| Parámetro | Descripción |
|---|---|
| `--cache` | Usa `requests-cache` para no repetir peticiones al probar |

Requiere `dataset/population.csv` (apartado 2). Selecciona una de cada k = n // 100 imágenes JPEG
(ordenadas por fecha y título), pide en lotes de 50 títulos las dos capas de metadatos y escribe
`sample.csv`, `metadata_description.csv`, `metadata_exif_api.csv` y el bloque `sample` de `results.json`.
Si la URL de un lote resultase demasiado larga (HTTP 414), se repite por POST.

### Apartado 4

```bash
python source/apartado4.py
```

Requiere los ficheros del apartado 3. Selecciona 20 ficheros de la muestra de más de 500 KiB
(los 10 menores con marca de cámara y los 10 menores sin ella), los descarga en `downloads/`
(no se sube al repositorio; si ya existe con el SHA-1 esperado, no se vuelve a pedir), lee sus metadatos
con Pillow y escribe `dataset/metadata_file.csv` y el bloque `files` de `results.json`.

### Política de uso de la API

Todas las peticiones salen de `source/common.py` (`ApiClient`): se envían en serie,
con una pausa aleatoria de 2 a 5 segundos entre ellas, con el User-Agent
`TCVD-PR2-UOC/1.0 (practica universitaria UOC; aroldan@uoc.edu) python-requests/<versión>`,
reintentos ante 429 y 5xx según `Retry-After` (60 s si no viene, duplicando la espera) y
comprobación del cuerpo de las respuestas 200. Cada petición se añade a `dataset/requests.csv`.

### Validación

```bash
python validar.py .
```
