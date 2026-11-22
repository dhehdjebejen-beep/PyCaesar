"""
tools/download_syzygy.py — Скачивание эндшпильных баз Syzygy (3, 4 или 5 фигур)
с официального зеркала Lichess в каталог `syzygy/`.

Использование:
    python tools/download_syzygy.py --pieces 4     # 3- и 4-фигурные (~4.4 МБ)
    python tools/download_syzygy.py --pieces 5     # + 5-фигурные (~938 МБ)
"""

import argparse
import os
import urllib.request

BASE_WDL = "https://tablebase.lichess.ovh/tables/standard/3-4-5-wdl/"
BASE_DTZ = "https://tablebase.lichess.ovh/tables/standard/3-4-5-dtz/"
PIECES = ["Q", "R", "B", "N", "P"]


def build_names(max_pieces: int) -> list[str]:
    names: list[str] = []
    if max_pieces >= 3:
        names.extend(f"K{p}vK" for p in PIECES)
    if max_pieces >= 4:
        for i, p1 in enumerate(PIECES):
            for p2 in PIECES[i:]:
                names.append(f"K{p1}{p2}vK")
                names.append(f"K{p1}vK{p2}")
    if max_pieces >= 5:
        for i, p1 in enumerate(PIECES):
            for j in range(i, len(PIECES)):
                p2 = PIECES[j]
                for k in range(j, len(PIECES)):
                    p3 = PIECES[k]
                    names.append(f"K{p1}{p2}{p3}vK")
        for i, p1 in enumerate(PIECES):
            for j in range(i, len(PIECES)):
                p2 = PIECES[j]
                for p3 in PIECES:
                    names.append(f"K{p1}{p2}vK{p3}")
    return names


def download_file(url: str, dst: str) -> int:
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return 0
    req = urllib.request.Request(url, headers={"User-Agent": "PyCaesar-Syzygy-Downloader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(dst, "wb") as f:
            f.write(data)
        return len(data)
    except Exception as e:
        print(f"  [skip] {os.path.basename(dst)}: {e}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Скачивание эндшпильных баз Syzygy для PyCaesar")
    parser.add_argument("--pieces", type=int, choices=[3, 4, 5], default=4,
                        help="Максимальное число фигур (по умолчанию 4)")
    parser.add_argument("--out", default=None, help="Каталог назначения (по умолчанию ./syzygy)")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = args.out or os.path.join(root, "syzygy")
    os.makedirs(out_dir, exist_ok=True)

    names = build_names(args.pieces)
    total_bytes = 0
    downloaded = 0
    print(f"Каталог: {out_dir} | Наборов: {len(names)} (до {args.pieces} фигур)")
    for idx, name in enumerate(names, 1):
        for ext, base in (("rtbw", BASE_WDL), ("rtbz", BASE_DTZ)):
            sz = download_file(f"{base}{name}.{ext}", os.path.join(out_dir, f"{name}.{ext}"))
            if sz:
                total_bytes += sz
                downloaded += 1
        if idx % 10 == 0 or idx == len(names):
            print(f"  Прогресс: {idx}/{len(names)} (скачано новых файлов: {downloaded}, {total_bytes / 1024 / 1024:.2f} МБ)")


if __name__ == "__main__":
    main()
