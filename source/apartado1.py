"""Apartado 1: ventana de tiempo, User-Agent y ficheros de partida.

Inicializa results.json (bloques "api" y "window") y dataset/requests.csv
Como tal no hace peticiones a la API, unicamente prepara los ficheros de partida para el resto de apartados.

Forma de uso:
    python source/apartado1.py --seed 1945750

"""
import argparse

from source.common import (REQUESTS_PATH, build_user_agent, compute_window,
                    init_requests_csv, load_results, save_results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True,
                        help="semilla: el menor de los números de estudiante (IDP) del grupo")
    args = parser.parse_args()

    results = load_results()
    results["api"] = {
        "key": "",   # no hace falta clave para leer datos públicos
        "user_agent": build_user_agent(),
        # Peticiones de este apartado: se conserva el contador si ya existía.
        "requests": results.get("api", {}).get("requests", 0),
    }
    results["window"] = compute_window(args.seed)
    save_results(results)
    init_requests_csv()

    print("results.json actualizado:", results["window"])
    print("User-Agent:", results["api"]["user_agent"])
    print("Registro de peticiones:", REQUESTS_PATH)


if __name__ == "__main__":
    main()
