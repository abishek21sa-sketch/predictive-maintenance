from __future__ import annotations

import argparse
import urllib.request
import zipfile
from pathlib import Path

URL = "https://data.nasa.gov/docs/legacy/CMAPSSData.zip"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="data/raw/CMAPSSData.zip")
    args = p.parse_args()
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(URL, out)
    target = out.parent / "CMAPSSData"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(out) as zf:
        zf.extractall(target)
    print(f"Downloaded and extracted NASA C-MAPSS to {target}")


if __name__ == "__main__":
    main()
