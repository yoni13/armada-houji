#!/usr/bin/env python3
"""Export RIFE 4.9 (arch 4.7, MIT, hzwer/Practical-RIFE) to a fixed-shape ONNX
graph for the Hexagon NPU feasibility test.

Inputs img0/img1 are NCHW float32 in [0, 1]; the output is the frame at
timestep 0.5. Uses ComfyUI-Frame-Interpolation's rife_arch.py (MIT) for the
network definition; its ComfyUI device helper is stubbed to CPU.

--scale 1 runs the four flow blocks at 1/8, 1/4, 1/2, 1/1 of the input
(Practical-RIFE default); --scale 0.5 runs them at half that resolution.
"""
import argparse
import sys
import types
from pathlib import Path

import torch

comfy = types.ModuleType('comfy')
mm = types.ModuleType('comfy.model_management')
mm.get_torch_device = lambda: torch.device('cpu')
comfy.model_management = mm
sys.modules['comfy'] = comfy
sys.modules['comfy.model_management'] = mm


class Midpoint(torch.nn.Module):
    def __init__(self, net, scale_list):
        super().__init__()
        self.net = net
        self.scale_list = scale_list

    def forward(self, img0, img1):
        return self.net(img0, img1, timestep=0.5, scale_list=list(self.scale_list),
                        training=False, fastmode=True, ensemble=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('arch', type=Path, help='rife_arch.py')
    p.add_argument('weights', type=Path, help='rife49.pth')
    p.add_argument('output', type=Path)
    p.add_argument('--width', type=int, default=1280)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--scale', type=float, default=1.0)
    a = p.parse_args()
    sys.path.insert(0, str(a.arch.parent))
    rife_arch = __import__(a.arch.stem)
    net = rife_arch.IFNet(arch_ver='4.7')
    state = torch.load(a.weights, map_location='cpu', weights_only=True)
    state = {k.removeprefix('module.'): v for k, v in state.items()}
    keys = set(net.state_dict())
    net.load_state_dict({k: v for k, v in state.items() if k in keys}, strict=True)
    net.eval()
    scale_list = [8 / a.scale, 4 / a.scale, 2 / a.scale, 1 / a.scale]
    model = Midpoint(net, scale_list).eval()
    img0 = torch.rand(1, 3, a.height, a.width)
    img1 = torch.roll(img0, shifts=(3, 5), dims=(2, 3))
    with torch.no_grad():
        reference = model(img0, img1)
        torch.onnx.export(model, (img0, img1), str(a.output), opset_version=17, dynamo=False,
                          input_names=['img0', 'img1'], output_names=['middle'], do_constant_folding=True)
    torch.save({'img0': img0, 'img1': img1, 'middle': reference}, a.output.with_suffix('.reference.pt'))
    print(f'exported {a.output} {a.width}x{a.height} scale_list={scale_list} output {tuple(reference.shape)}')


if __name__ == '__main__':
    main()
