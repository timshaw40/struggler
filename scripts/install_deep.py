"""Install what the deep bot needs into data/deep/ (git-ignored).

    python scripts/install_deep.py --engine                 # ts_engine.wasm from this repo's release
    python scripts/install_deep.py --model path/to/model.onnx
    python scripts/install_deep.py --model https://.../model.onnx

The engine is DeepStruggle's rules compiled to WebAssembly by CI
(.github/workflows/deep-engine.yml) and published as the `deep-engine` release.
The model is a DeepStruggle ONNX export; it is checked before it is installed:
the right format, a plain (not merged-influence) action view, and an
observation width the engine produces.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
DEST = ROOT / "data" / "deep"
ENGINE_URL = "https://github.com/timshaw40/struggler/releases/download/deep-engine/ts_engine.wasm"


def fetch(source: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
        if source.startswith(("http://", "https://")):
            with urllib.request.urlopen(source) as response:
                shutil.copyfileobj(response, tmp)
        else:
            with open(source, "rb") as f:
                shutil.copyfileobj(f, tmp)
    Path(tmp.name).replace(dest)


def check_model(path: Path) -> str:
    from struggler.bots.deep.backend import load_backend
    from struggler.bots.deep.player import OnnxPolicy

    policy = OnnxPolicy(str(path))  # refuses a merged-influence network
    meta = policy.session.get_modelmeta().custom_metadata_map
    if meta.get("ts.format") != "ts-onnx-v1":
        raise SystemExit(f"{path} is not a DeepStruggle export (ts.format={meta.get('ts.format')!r})")
    # `ts.obs_size` is the base layout's width; feature blocks append to it.
    base = len(load_backend().observation(1, 0))
    if meta.get("ts.obs_size") and int(meta["ts.obs_size"]) != base:
        raise SystemExit(f"model expects a {meta['ts.obs_size']}-float base observation; "
                         f"this engine writes {base}: engine and model are from different epochs")
    return policy.label


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", action="store_true", help="download ts_engine.wasm")
    ap.add_argument("--engine-url", default=ENGINE_URL)
    ap.add_argument("--model", help="a DeepStruggle .onnx file or URL")
    args = ap.parse_args()
    if not (args.engine or args.model):
        ap.error("nothing to install: pass --engine and/or --model")
    if args.engine:
        fetch(args.engine_url, DEST / "ts_engine.wasm")
        print(f"engine: {DEST / 'ts_engine.wasm'}")
    if args.model:
        staged = DEST / "model.onnx.new"
        fetch(args.model, staged)
        try:
            label = check_model(staged)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        staged.replace(DEST / "model.onnx")
        print(f"model: {DEST / 'model.onnx'} ({label})")
    print("play it: python scripts/serve_ui.py --us human --ussr deep")


if __name__ == "__main__":
    main()
