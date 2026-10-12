#!/usr/bin/env python3
"""Deterministic linear-RGB temporal fixture with exact motion and Halton jitter."""
import argparse
import json
from pathlib import Path
import struct
import numpy as np


def halton(index, base):
    result=0.;factor=1.
    while index:
        factor/=base;result+=factor*(index%base);index//=base
    return result-.5


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    p.add_argument('--width',type=int,default=128);p.add_argument('--height',type=int,default=64)
    p.add_argument('--frames',type=int,default=3);a=p.parse_args()
    w,h=a.width,a.height
    if w<16 or h<16 or w%16 or h%16 or a.frames<1:p.error('positive dimensions divisible by 16 and positive frame count required')
    rw,rh=w*2//3,h*2//3
    a.output.mkdir(parents=True,exist_ok=True)
    for n in range(a.frames):
        d=a.output/('f%02d'%n);d.mkdir(exist_ok=True)
        jx,jy=halton(n+1,2),halton(n+1,3)
        # Scene translates right by one render pixel per frame. All layers share
        # the motion; zero depth and stationary-camera tests are separate cases.
        yy,xx=np.mgrid[:rh,:rw];u=(xx+jx-n)/rw;v=(yy+jy)/rh
        color=np.empty((rh,rw,4),np.float32)
        color[:,:,0]=.25+.2*np.sin(u*2*np.pi*9)
        color[:,:,1]=.25+.2*np.cos(v*2*np.pi*7)
        color[:,:,2]=.15+.35*((np.floor(u*16)+np.floor(v*8))%2)
        color[:,:,3]=1
        color.astype('<f2').tofile(d/'color.bin')
        np.full((rh,rw),.5,dtype='<f4').tofile(d/'depth.bin')
        mv=np.zeros((rh,rw,2),dtype='<f2');mv[:,:,0]=-1 if n else 0;mv.tofile(d/'mv.bin')
        floats=[1/w,1/h,w/rw,h/rh,rw/w,rh/h,jx,jy,1/rw,1/rh,w,h,rw,rh,0,0]
        payload=struct.pack('<16f5I2fI2f',*floats,w,h,int(n==0),rw,rh,1.,1.,0,0.,0.)
        assert len(payload)==104;(d/'constants.bin').write_bytes(payload)
    (a.output/'meta.json').write_text(json.dumps({'render':[rw,rh],'output':[w,h],'frames':a.frames,'colorspace':'linear','motion':'previous-minus-current render pixels','preset':'quality'},indent=2)+'\n')


if __name__=='__main__':main()
