#!/usr/bin/env python3
"""Emit a QNN C graph with explicit fixed-point boundaries from the candidate ONNX.

This bypasses QAIRT's QDQ-removal pass. Only the FSR neural-core operator set
is accepted. Unquantized intermediates use HTP FP16; comparisons are required.
"""
import argparse
from pathlib import Path
import numpy as np
import onnx
from onnx import numpy_helper, helper


class Emitter:
    def __init__(self, model, tap=None, int16_residual=False, int8_residual=False):
        self.model = onnx.shape_inference.infer_shapes(model)
        self.constants = {t.name: numpy_helper.to_array(t) for t in model.graph.initializer}
        self.shapes = {v.name: [d.dim_value for d in v.type.tensor_type.shape.dim]
                       for v in [*self.model.graph.input, *self.model.graph.value_info, *self.model.graph.output]}
        self.users = {}
        for node in model.graph.node:
            for name in node.input:
                self.users.setdefault(name, []).append(node)
        self.aliases, self.tensors = {}, []
        self.declarations, self.calls = [], []
        self.skip = set()
        self.output_name = model.graph.output[0].name
        self.tap = tap
        self.int16_residual = int16_residual
        self.int8_residual = int8_residual

    def shape(self, name):
        s = self.shapes.get(name)
        if s is None:
            s = list(self.constants[name].shape)
        return [s[0], s[2], s[3], s[1]] if len(s) == 4 else s

    def tensor(self, name, dims, scale=None, data=None, dtype=None, offset=-128, app=None):
        index = len(self.tensors)
        symbol = 't%d' % index
        dtype = dtype or (('UFIXED_POINT_16' if offset == -32768 else 'UFIXED_POINT_8') if scale is not None else 'FLOAT_16')
        self.declarations.append('static uint32_t %s_dims[] = {%s};' % (symbol, ','.join(map(str,dims))))
        size, pointer = 0, 'NULL'
        if data is not None:
            data = np.ascontiguousarray(data)
            payload = data.tobytes()
            self.declarations.append('static const unsigned char %s_data[] = {%s};' % (symbol, ','.join(map(str,payload))))
            size, pointer = len(payload), '(void *)%s_data' % symbol
        if self.tap:
            if app == 'READ': app = None
            if name == self.tap: app = 'READ'
        kind = 'STATIC' if data is not None else ('APP_'+app if app else 'NATIVE')
        self.calls.append('    ts[%d] = make_tensor("%s", QNN_TENSOR_TYPE_%s, QNN_DATATYPE_%s, %s_dims, %d, %.17gf, %d, %s, %d);' %
                          (index,name,kind,dtype,symbol,len(dims),scale or 0.,offset if scale else 0,pointer,size))
        self.calls[-1] = self.calls[-1].replace(' 0f,',' 0.0f,')
        self.calls.append('    TRY(api->tensorCreateGraphTensor(graph, &ts[%d]));' % index)
        result = {'index':index,'scale':scale,'offset':offset,'dims':dims,'dtype':dtype}
        self.tensors.append(result)
        return result

    def emit_op(self, kind, inputs, output, params=()):
        index = len(self.calls)
        self.calls.append('    {')
        self.calls.append('      Qnn_Tensor_t ins[] = {%s};' % ','.join('ts[%d]'%i['index'] for i in inputs))
        self.calls.append('      Qnn_Param_t params[%d];' % max(1,len(params)))
        for n,(name,value) in enumerate(params):
            if isinstance(value,int):
                self.calls.append('      params[%d] = scalar_param("%s", %du);'%(n,name,value))
            else:
                a=np.array(value,dtype=np.uint32)
                self.declarations.append('static uint32_t p%d_%d[] = {%s};'%(index,n,','.join(map(str,a.flat))))
                self.declarations.append('static uint32_t pd%d_%d[] = {%s};'%(index,n,','.join(map(str,a.shape))))
                self.calls.append('      params[%d] = tensor_param("%s", p%d_%d, pd%d_%d, %d, %d);'%(n,name,index,n,index,n,a.ndim,a.nbytes))
        self.calls.append('      TRY(add_op(api, graph, "node_%d", "%s", ins, %d, &ts[%d], params, %d));' %
                          (index,kind,len(inputs),output['index'],len(params)))
        self.calls.append('    }')

    def as_float(self,t):
        if t['scale'] is None:return t
        result=self.tensor('float_%d'%len(self.tensors),t['dims'])
        self.emit_op('Convert',[t],result)
        return result

    def as_fixed16(self,t,scale=None):
        if t['scale'] is None:raise ValueError('Cannot infer fixed16 scale for float tensor')
        scale=scale or (t['scale']/256 if t['offset']!=-32768 else t['scale'])
        if t['offset']==-32768 and t['scale']==scale:return t
        result=self.tensor('wide_%d'%len(self.tensors),t['dims'],scale=scale,offset=-32768)
        self.emit_op('Convert',[t],result)
        return result

    def constant(self,name):
        if name in self.aliases:return self.aliases[name]
        data=self.constants[name]
        if data.ndim==4:data=data.transpose(0,2,3,1)
        result=self.tensor(name,list(data.shape),data=data.astype('<f2'))
        self.aliases[name]=result
        return result

    def output(self,node):
        name=node.output[0]
        users=self.users.get(name,[])
        if len(users)==1 and users[0].op_type=='QuantizeLinear':
            q=users[0];scale=float(self.constants[q.input[1]])
            offset=-int(self.constants[q.input[2]])
            self.skip.add(q.output[0])
            result=self.tensor(q.output[0],self.shape(name),scale=scale,offset=offset)
            self.aliases[q.output[0]]=result
        else:
            nearby_scales=[]
            if (self.int16_residual or self.int8_residual) and node.op_type in ('Conv','ConvTranspose','Add') and name!=self.output_name:
                queue=list(users)
                seen=set()
                while queue:
                    n=queue.pop(0)
                    if n.output[0] in seen:continue
                    seen.add(n.output[0])
                    if n.op_type=='QuantizeLinear':nearby_scales.append(float(self.constants[n.input[1]]))
                    elif n.op_type in ('Slice','Add','Relu'):queue.extend(self.users.get(n.output[0],[]))
            if nearby_scales:
                result=self.tensor(name,self.shape(name),scale=max(nearby_scales)*(4 if self.int8_residual else 1/64),offset=-128 if self.int8_residual else -32768)
            else:
                result=self.tensor(name,self.shape(name),app='READ' if name==self.output_name else None)
        self.aliases[name]=result
        return result

    def build(self):
        for node in self.model.graph.node:
            name=node.output[0];op=node.op_type
            attrs={a.name:helper.get_attribute_value(a) for a in node.attribute}
            if name in self.skip:continue
            if op=='DequantizeLinear':
                src=node.input[0]
                if src in self.constants:
                    self.aliases[name]={'weight': self.constants[src], 'scale':float(self.constants[node.input[1]]),
                                       'offset':-int(self.constants[node.input[2]])}
                elif src in self.aliases:self.aliases[name]=self.aliases[src]
                else:
                    self.aliases[src]=self.tensor(src,self.shape(src),scale=float(self.constants[node.input[1]]),
                        offset=-int(self.constants[node.input[2]]),app='WRITE')
                    self.aliases[name]=self.aliases[src]
                continue
            if op=='QuantizeLinear':
                x=self.aliases[node.input[0]]
                y=self.tensor(name,self.shape(name),scale=float(self.constants[node.input[1]]),offset=-int(self.constants[node.input[2]]))
                self.emit_op('Convert',[x],y);self.aliases[name]=y
                continue
            if op in ('Conv','ConvTranspose'):
                x=self.aliases[node.input[0]]; w=self.aliases[node.input[1]]
                y=self.output(node)
                # Conv outputs feeding a residual Add are real-valued. HTP's
                # float path needs float inputs and explicit conversion first.
                quant = x['scale'] is not None and y['scale'] is not None
                if quant and y['offset']==-32768:
                    x=self.as_fixed16(x)
                weight=w['weight'].transpose(2,3,1,0) if op=='Conv' else w['weight'].transpose(2,3,0,1)
                if quant:
                    wt=self.tensor(name+'_weight',list(weight.shape),scale=w['scale'],data=weight,offset=w['offset'],
                                   dtype='SFIXED_POINT_8' if weight.dtype==np.int8 else 'UFIXED_POINT_8')
                else:
                    x=self.as_float(x)
                    weight=((weight.astype(np.float32)+w['offset'])*w['scale']).astype('<f2')
                    wt=self.tensor(name+'_weight',list(weight.shape),data=weight)
                ins=[x,wt]
                if len(node.input)>2:
                    bias=self.constants[node.input[2]]
                    if quant:
                        scale=x['scale']*w['scale']
                        raw=np.rint(bias.astype(np.float64)/scale)
                        if np.any(abs(raw)>2147483647):raise ValueError('Bias exceeds int32')
                        bt=self.tensor(name+'_bias',list(bias.shape),scale=scale,data=raw.astype('<i4'),dtype='SFIXED_POINT_32',offset=0)
                    else:bt=self.tensor(name+'_bias',list(bias.shape),data=bias.astype('<f2'))
                    ins.append(bt)
                pads=attrs.get('pads',[0,0,0,0])
                params=[('stride',attrs.get('strides',[1,1])),('pad_amount',[[pads[0],pads[2]],[pads[1],pads[3]]]),('group',attrs.get('group',1))]
                params.append(('dilation',attrs.get('dilations',[1,1])) if op=='Conv' else ('output_padding',attrs.get('output_padding',[0,0])))
                self.emit_op('Conv2d' if op=='Conv' else 'TransposeConv2d',ins,y,params)
            elif op=='Relu':
                x=self.aliases[node.input[0]]
                y=self.tensor(name,self.shape(name),scale=x['scale'],offset=x['offset'])
                self.emit_op('Relu',[x],y);self.aliases[name]=y
            elif op=='Add':
                ins=[self.aliases[n] if n in self.aliases else self.constant(n) for n in node.input]
                y=self.output(node)
                if all(i['scale'] is not None for i in ins) and any(i['offset']==-32768 for i in ins):
                    ins=[self.as_fixed16(i) for i in ins]
                    if y['offset']!=-32768:
                        tmp=self.tensor(name+'_wide',self.shape(name),scale=y['scale']/64,offset=-32768)
                        self.emit_op('ElementWiseAdd',ins,tmp);self.emit_op('Convert',[tmp],y);continue
                if not all(i['scale'] is not None for i in ins):
                    ins=[self.as_float(i) for i in ins]
                    if y['scale'] is not None:
                        tmp=self.tensor(name+'_real',self.shape(name))
                        self.emit_op('ElementWiseAdd',ins,tmp);self.emit_op('Convert',[tmp],y);continue
                self.emit_op('ElementWiseAdd',ins,y)
            elif op=='Concat':
                ins=[self.aliases[n] for n in node.input]
                same=all(i['scale']==ins[0]['scale'] and i['offset']==ins[0]['offset'] for i in ins)
                if not same:
                    if self.int8_residual and all(i['scale'] is not None for i in ins):
                        scale=max(i['scale'] for i in ins)
                        converted=[]
                        for i in ins:
                            if i['scale']==scale:converted.append(i);continue
                            t=self.tensor('rescaled_%d'%len(self.tensors),i['dims'],scale=scale)
                            self.emit_op('Convert',[i],t);converted.append(t)
                        ins=converted
                    elif self.int16_residual and all(i['scale'] is not None for i in ins):
                        scale=max(i['scale']*(256 if i['offset']==-32768 else 1) for i in ins)/256
                        ins=[self.as_fixed16(i,scale) for i in ins]
                    else:ins=[self.as_float(i) for i in ins]
                y=self.tensor(name,self.shape(name),scale=ins[0]['scale'],offset=ins[0]['offset'])
                self.emit_op('Concat',ins,y,[('axis',3)]);self.aliases[name]=y
            elif op=='Slice':
                x=self.aliases[node.input[0]]
                start,end,axis=(self.constants[n].item() for n in node.input[1:4])
                if axis!=1:raise ValueError('Only channel slicing is implemented')
                ranges=[[0,int(dim),1] for dim in x['dims']];ranges[3]=[int(start),int(end),1]
                y=self.tensor(name,self.shape(name),scale=x['scale'],offset=x['offset'])
                self.emit_op('StridedSlice',[x],y,[('ranges',ranges)]);self.aliases[name]=y
            elif op=='Cast':
                if attrs['to']!=onnx.TensorProto.FLOAT16:raise ValueError('Unexpected cast')
                self.aliases[name]=self.as_float(self.aliases[node.input[0]])
            else:raise ValueError('Unsupported operator '+op)
        code=['/* Generated from FSR4 candidate ONNX; do not edit. */',*self.declarations,
              'static int compose_fsr(QNN_INTERFACE_VER_TYPE *api, Qnn_GraphHandle_t graph, Qnn_Tensor_t *input, Qnn_Tensor_t *output) {',
              '    Qnn_Tensor_t ts[%d];'%len(self.tensors),*self.calls,
              '    *input=ts[%d];'%self.aliases[self.model.graph.input[0].name]['index'],
              '    *output=ts[%d];'%self.aliases[self.tap or self.output_name]['index'], '    return 0;','}']
        return '\n'.join(code)+'\n'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('model',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--tap', help='Expose one exact intermediate tensor for numerical diagnosis')
    p.add_argument('--int16-residual', action='store_true', help='Experimental W8A16 residual paths instead of FP16 fallback')
    p.add_argument('--int8-residual', action='store_true', help='Experimental W8A8 residual paths with extra quantization')
    a=p.parse_args()
    if a.int8_residual and a.int16_residual:p.error('select at most one residual format')
    e=Emitter(onnx.load(a.model),tap=a.tap,int16_residual=a.int16_residual,int8_residual=a.int8_residual);a.output.write_text(e.build())
    print('Generated',len(e.tensors),'explicit QNN tensors')


if __name__=='__main__':main()
