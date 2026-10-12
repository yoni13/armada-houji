#!/usr/bin/env python3
"""Evaluate a single captured feature tensor using CPU ONNX (no QNN)."""
import argparse
from pathlib import Path
import numpy as np
import onnxruntime as ort


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('model',type=Path);p.add_argument('input',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();options=ort.SessionOptions();options.intra_op_num_threads=8
    options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    session=ort.InferenceSession(str(a.model),options,providers=['CPUExecutionProvider'])
    _,c,h,w=session.get_inputs()[0].shape
    x=np.fromfile(a.input,np.uint8).reshape(1,h,w,c).transpose(0,3,1,2)
    y=session.run([session.get_outputs()[0].name],{session.get_inputs()[0].name:np.ascontiguousarray(x)})[0]
    if not np.isfinite(y).all():raise ValueError('Non-finite ONNX output')
    np.ascontiguousarray(y[0].transpose(1,2,0),dtype='<f2').tofile(a.output)
    print('CPU reference parameters written')


if __name__=='__main__':main()
