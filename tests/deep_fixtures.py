"""A stand-in DeepStruggle ONNX model for tests: the real export's interface
(`obs`, `mask` -> `logits`, `v_win`, `v_vp`, the `ts.*` metadata) with random
weights. It plays badly on purpose; tests check plumbing, not strength."""
from pathlib import Path


def write_random_policy(path: Path, obs_width: int = 3824, actions: int = 220, seed: int = 0) -> Path:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(seed)
    weights = numpy_helper.from_array(rng.normal(0, 0.05, (obs_width, actions)).astype(np.float32), "W")
    nodes = [
        helper.make_node("MatMul", ["obs", "W"], ["raw"]),
        helper.make_node("Cast", ["mask"], ["maskf"], to=TensorProto.FLOAT),
        helper.make_node("Sub", ["maskf", "one"], ["off"]),            # 0 legal, -1 illegal
        helper.make_node("Mul", ["off", "big"], ["penalty"]),
        helper.make_node("Add", ["raw", "penalty"], ["logits"]),
        helper.make_node("ReduceMean", ["raw"], ["v_win"], axes=[1], keepdims=0),
        helper.make_node("ReduceMean", ["raw"], ["v_vp"], axes=[1], keepdims=0),
    ]
    consts = [numpy_helper.from_array(np.array(1.0, np.float32), "one"),
              numpy_helper.from_array(np.array(1e9, np.float32), "big")]
    graph = helper.make_graph(
        nodes, "random_policy",
        [helper.make_tensor_value_info("obs", TensorProto.FLOAT, ["batch", obs_width]),
         helper.make_tensor_value_info("mask", TensorProto.UINT8, ["batch", actions])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", actions]),
         helper.make_tensor_value_info("v_win", TensorProto.FLOAT, ["batch"]),
         helper.make_tensor_value_info("v_vp", TensorProto.FLOAT, ["batch"])],
        initializer=[weights, *consts])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
    for k, v in {"ts.format": "ts-onnx-v1", "ts.obs_size": str(obs_width), "ts.action_size": str(actions),
                 "ts.merged_influence": "false", "ts.obs_features": "0", "ts.label": "random-test"}.items():
        model.metadata_props.add(key=k, value=v)
    onnx.save(model, str(path))
    return path
