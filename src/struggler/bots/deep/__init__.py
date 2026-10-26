"""The `deep` bot: DeepStruggle's network playing through struggler's API.

Struggler stays the referee. To decide, the player rebuilds the position in
DeepStruggle's own engine (`ts_engine`, native on Linux or WebAssembly
elsewhere), asks the network, and answers with one of struggler's legal
options. See docs/BOTS.md, "Deep bot".

Modules:

- `ids` maps countries, cards and sides between the two engines.
- `backend` is the narrow interface to a DeepStruggle engine build.
- `boundary` writes a struggler position at a headline or action-round start
  as a DeepStruggle `ts_save_v2` dict.
- `replay` groups struggler events into DeepStruggle micro-actions and steps
  them, with dice forced to the logged values.
"""


def missing() -> str | None:
    """Why the deep bot cannot run here, or None when it can: the packages
    (`pip install -e '.[deep]'`), a model, and an engine build."""
    import importlib.util
    import os
    from pathlib import Path

    data = Path(__file__).resolve().parents[4] / "data" / "deep"
    if importlib.util.find_spec("onnxruntime") is None:
        return "onnxruntime is not installed (pip install -e '.[deep]')"
    if not Path(os.environ.get("STRUGGLER_DEEP_MODEL") or data / "model.onnx").exists():
        return "no model (python scripts/install_deep.py --model <file.onnx>)"
    if os.environ.get("STRUGGLER_DEEP_ENGINE") or os.environ.get("STRUGGLER_DEEP_SHARED_LIB"):
        return None
    if importlib.util.find_spec("wasmtime") is None:
        return "wasmtime is not installed (pip install -e '.[deep]')"
    if not Path(os.environ.get("STRUGGLER_DEEP_WASM") or data / "ts_engine.wasm").exists():
        return "no engine (python scripts/install_deep.py --engine)"
    return None
