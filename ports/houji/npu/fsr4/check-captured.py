#!/usr/bin/env python3
"""Evaluate ONNX using actual GPU features dumped by the standalone NPU pipeline.

Outputs are neural reconstruction parameters, not image-quality scores. Feed the
reference parameters through the same post shader for RGB comparisons.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import onnxruntime as ort


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('model',type=Path);p.add_argument('capture',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    options=ort.SessionOptions();options.intra_op_num_threads=8;options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    session=ort.InferenceSession(str(a.model),options,providers=['CPUExecutionProvider'])
    _,c,h,w=session.get_inputs()[0].shape
    report={'reference':'CPU ONNX fed the same GPU-preprocessed features','amd_golden_parity_verified':False,'frames':{}}
    for source in sorted(a.capture.glob('f*-features-u8.bin')):
        tag=source.name.split('-')[0]
        x=np.fromfile(source,'u1').reshape(1,h,w,c).transpose(0,3,1,2)
        ref=session.run(None,{session.get_inputs()[0].name:np.ascontiguousarray(x)})[0][0].transpose(1,2,0).astype('<f2')
        got=np.fromfile(a.capture/(tag+'-neural.bin'),'<f2').reshape(ref.shape)
        if not np.isfinite(got).all() or not np.isfinite(ref).all():raise ValueError('Non-finite neural output')
        delta=got.astype('f4')-ref.astype('f4')
        metrics={'max_absolute':float(abs(delta).max()),'mean_absolute':float(abs(delta).mean()),'rmse':float(np.sqrt(np.mean(delta**2)))}
        report['frames'][tag]=metrics
        state=a.output/tag;state.mkdir(exist_ok=True);ref.tofile(state/'neural.bin')
        (state/'reprojected.bin').write_bytes((a.capture/(tag+'-reprojected.bin')).read_bytes())
        print(tag,metrics,flush=True)
    (a.output/'neural-comparison.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
