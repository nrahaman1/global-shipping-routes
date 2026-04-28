"""Fetch the density raster + WPI ports file from the Hugging Face dataset
mirror. Idempotent: skips files that already exist.

Set the HF_DATASET_REPO env var to point at your own mirror, e.g.
    HF_DATASET_REPO=nafizrahaman/global-shipping-data
"""
import os
import shutil
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = os.environ.get("HF_DATASET_REPO", "nafizrahaman/global-shipping-data")
ROOT = Path(__file__).resolve().parent.parent
(ROOT / "work").mkdir(exist_ok=True)

FILES = [
    ("density_log_2km.tif", ROOT / "work" / "density_log_2km.tif"),
    ("world_port_index_25th.gpkg", ROOT / "world_port_index_25th.gpkg"),
]


def main() -> None:
    for fname, dst in FILES:
        if dst.exists():
            print(f"  exists: {dst.name}")
            continue
        print(f"  downloading {fname} from {REPO} ...")
        path = hf_hub_download(repo_id=REPO, filename=fname, repo_type="dataset")
        # hf_hub_download returns a cache path; copy to the project location.
        shutil.copy2(path, dst)
        print(f"  -> {dst}")


if __name__ == "__main__":
    main()
    print("done.")
