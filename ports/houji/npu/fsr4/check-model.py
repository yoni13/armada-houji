#!/usr/bin/env python3
"""Compare the candidate ONNX neural core with the imported CPU simulation.

The imported simulation's rounding is corrected to HLSL ties-to-even here.
This comparison detects layout/scale mistakes, but is not AMD GPU golden parity.
"""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('model', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import onnxruntime as ort
    sys.path.insert(0, str(args.work.resolve()/'hexagon_reference/model/sim'))
    import fsr4_sim as sim
    sim.round_half_away = lambda x: np.rint(np.asarray(x, dtype=np.float32)).astype(np.int64)
    ties = np.array([0.5, 1.5, 2.5, -0.5, -1.5, -2.5], np.float32)
    assert np.array_equal(sim.round_half_away(ties), [0, 2, 2, 0, -2, -2])
    layers = sim.load_with_pass0_scale(sim.load_layers())
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    session = ort.InferenceSession(str(args.model), options, providers=['CPUExecutionProvider'])
    _, channels, height, width = session.get_inputs()[0].shape
    rng = np.random.default_rng(7)
    cases = {
        'zero': np.zeros((height, width, channels), np.int8),
        'random': rng.integers(-100, 101, (height, width, channels), dtype=np.int8),
        'positive_saturation': np.full((height, width, channels), 127, np.int8),
        'negative_saturation': np.full((height, width, channels), -128, np.int8),
    }
    report = {'comparison': 'CPU ONNX against ties-to-even corrected reference simulation',
              'amd_golden_parity_verified': False, 'htp_execution_verified': False, 'cases': {}}
    for name, signed in cases.items():
        expected = sim.run_graph({'q': signed, 's': sim.S_IN}, layers).astype(np.float32)
        storage = signed if session.get_inputs()[0].type == 'tensor(int8)' else (signed.astype(np.int16)+128).astype(np.uint8)
        encoded = np.ascontiguousarray(storage.transpose(2, 0, 1)[None])
        result = session.run([session.get_outputs()[0].name], {session.get_inputs()[0].name: encoded})[0]
        result = result[0].transpose(1, 2, 0).astype(np.float32)
        assert result.shape == expected.shape and np.isfinite(result).all()
        delta = result-expected
        report['cases'][name] = {'max_absolute': float(np.abs(delta).max()),
                                 'mean_absolute': float(np.abs(delta).mean()),
                                 'rmse': float(np.sqrt(np.mean(delta**2))),
                                 'bit_equal_fraction': float((result == expected).mean())}
        if name == 'random':
            # Reusable QNN test input; desired NHWC conversion uses the second file.
            encoded.tofile(args.report.with_suffix('.input-nchw.raw'))
            np.ascontiguousarray(encoded.transpose(0, 2, 3, 1)).tofile(args.report.with_suffix('.input-nhwc.raw'))
            np.ascontiguousarray(result).astype('<f2').tofile(args.report.with_suffix('.output-nhwc-f16.raw'))
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
