#!/usr/bin/env python3
"""Extract AMD weights and build a static QDQ model of passes 1-13.

This is the neural core, not a complete image upscaler. Preprocessing/pass 0,
temporal history and reconstruction are separate GPU stages. The model uses
UINT8 storage with zero point 128 to represent AMD's signed INT8 activations.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path, help='Directory populated by fetch-sources.py')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=64, help='Neural input width (half the padded output width)')
    parser.add_argument('--height', type=int, default=32, help='Neural input height (half the padded output height)')
    parser.add_argument('--debug-tensors', action='store_true')
    parser.add_argument('--signed', action='store_true', help='Use signed INT8 QDQ and I/O for HTP comparison')
    parser.add_argument('--explicit-saturation', action='store_true', help='Test reference Clip/FP16 barriers against QNN QDQ removal')
    parser.add_argument('--float-calibration', action='store_true', help='Float graph for QAIRT calibration; not an INT8-fidelity model')
    args = parser.parse_args()
    if min(args.width, args.height) < 4 or args.width % 4 or args.height % 4:
        parser.error('neural dimensions must be positive multiples of four (two strided downscales)')

    import onnx
    import numpy as np
    work = args.work.resolve()
    reference = work/'hexagon_reference/model'
    env = dict(os.environ, FIDELITYFX_SDK_ROOT=str(work/'amd'))
    subprocess.run([sys.executable, str(reference/'extract/build_weights.py')], env=env, check=True)
    sys.path.insert(0, str(reference/'sim'))
    spec = importlib.util.spec_from_file_location('fsr4_onnx_reference', reference/'onnx/build_onnx.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The pinned exporter uses u8 zero point 128 internally but still declares
    # a signed input. Fix the public type; callers explicitly bias signed data.
    module.G.FIO = False
    module.G.FLOAT = args.float_calibration
    # Preserve QDQ saturation without adding fp16 round trips at each boundary.
    module.G.EXPLICIT_SAT = args.explicit_saturation
    module.G.DBG_TAPS = args.debug_tensors
    layers = module.S.load_with_pass0_scale(module.S.load_layers())
    model = module.build(layers, H2=args.height, W2=args.width, debug=args.debug_tensors)
    model.graph.input[0].type.tensor_type.elem_type = onnx.TensorProto.FLOAT if args.float_calibration else onnx.TensorProto.UINT8
    if args.signed:
        # Change both tensor storage and zero point, so real values stay equal.
        for i, tensor in enumerate(model.graph.initializer):
            if tensor.data_type == onnx.TensorProto.UINT8:
                value = onnx.numpy_helper.to_array(tensor)
                replacement = onnx.numpy_helper.from_array((value.astype(np.int16)-128).astype(np.int8), tensor.name)
                model.graph.initializer[i].CopyFrom(replacement)
        for value in model.graph.output:
            if value.type.tensor_type.elem_type == onnx.TensorProto.UINT8:
                value.type.tensor_type.elem_type = onnx.TensorProto.INT8
        model.graph.input[0].type.tensor_type.elem_type = onnx.TensorProto.INT8
    # AMD's final decoder casts the convolution result to half *before* adding
    # its half bias. ConvTranspose with a float bias followed by one final cast
    # loses this rounding boundary. Split only this final bias out of the op.
    final_cast = next(n for n in model.graph.node if n.output[0] == model.graph.output[0].name)
    final_conv = next(n for n in model.graph.node if n.output[0] == final_cast.input[0])
    assert final_conv.op_type == 'ConvTranspose' and len(final_conv.input) == 3
    bias_name = final_conv.input.pop()
    bias_initializer = next(i for i in model.graph.initializer if i.name == bias_name)
    bias = onnx.numpy_helper.to_array(bias_initializer)
    bias_half = onnx.numpy_helper.from_array(bias.astype(np.float16).reshape(1, -1, 1, 1), 'decoder_bias_f16')
    model.graph.initializer.remove(bias_initializer)
    model.graph.initializer.append(bias_half)
    output_name = final_cast.output[0]
    final_cast.output[0] = 'decoder_unbiased_f16'
    model.graph.node.append(onnx.helper.make_node('Add', ['decoder_unbiased_f16', bias_half.name], [output_name]))
    onnx.checker.check_model(model, full_check=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, args.output)
    metadata = {
        'model': 'fsr4_model_v07_i8_quality', 'stages': 'neural passes 1-13 only',
        'input': {'name': model.graph.input[0].name, 'shape': [1, 16, args.height, args.width],
                  'layout': 'NCHW', 'storage': 'float32' if args.float_calibration else ('int8' if args.signed else 'uint8'),
                  'zero_point': 0 if args.signed else 128,
                  'scale': float(np.float32(module.S.S_IN))},
        'output': {'name': model.graph.output[0].name, 'shape': [1, 8, args.height*2, args.width*2],
                   'layout': 'NCHW', 'storage': 'float16', 'meaning': 'reconstruction parameters, not RGB'},
        'debug_tensors': args.debug_tensors, 'htp_execution_verified': False,
        'explicit_saturation_barriers': args.explicit_saturation,
        'float_calibration_only': args.float_calibration,
        'amd_golden_parity_verified': False,
    }
    args.output.with_suffix('.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print('Wrote', args.output, 'and I/O metadata; ONNX structural validation passed')


if __name__ == '__main__':
    main()
