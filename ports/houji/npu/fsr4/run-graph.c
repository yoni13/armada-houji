/* Compile with -DFSR_GRAPH_HEADER='"/path/to/fsr-graph.h"'. */
#include <dlfcn.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "QnnInterface.h"
#include "HTP/QnnHtpGraph.h"

#define TRY(call) do { Qnn_ErrorHandle_t e=(call); if (e) { \
    fprintf(stderr,"%s: 0x%llx\n",#call,(unsigned long long)e); return 1; } } while(0)

static Qnn_Tensor_t make_tensor(const char *name, Qnn_TensorType_t type,
        Qnn_DataType_t dtype, uint32_t *dims, uint32_t rank, float scale,
        int32_t offset, void *data, uint32_t size)
{
    Qnn_Tensor_t t=QNN_TENSOR_INIT;
    t.v1.name=name; t.v1.type=type; t.v1.dataType=dtype;
    t.v1.rank=rank; t.v1.dimensions=dims;
    t.v1.memType=QNN_TENSORMEMTYPE_RAW;
    t.v1.clientBuf=(Qnn_ClientBuffer_t){data,size};
    if (scale>0) {
        t.v1.quantizeParams.encodingDefinition=QNN_DEFINITION_DEFINED;
        t.v1.quantizeParams.quantizationEncoding=QNN_QUANTIZATION_ENCODING_SCALE_OFFSET;
        t.v1.quantizeParams.scaleOffsetEncoding=(Qnn_ScaleOffset_t){scale,offset};
    }
    return t;
}

static Qnn_Param_t scalar_param(const char *name, uint32_t value)
{
    Qnn_Param_t p=QNN_PARAM_INIT;
    p.name=name; p.paramType=QNN_PARAMTYPE_SCALAR;
    p.scalarParam.dataType=QNN_DATATYPE_UINT_32; p.scalarParam.uint32Value=value;
    return p;
}

static Qnn_Param_t tensor_param(const char *name, uint32_t *data, uint32_t *dims,
                                uint32_t rank, uint32_t size)
{
    Qnn_Param_t p=QNN_PARAM_INIT;
    p.name=name; p.paramType=QNN_PARAMTYPE_TENSOR;
    p.tensorParam=make_tensor(name,QNN_TENSOR_TYPE_STATIC,QNN_DATATYPE_UINT_32,dims,rank,0,0,data,size);
    return p;
}

static Qnn_ErrorHandle_t add_op(QNN_INTERFACE_VER_TYPE *api, Qnn_GraphHandle_t graph,
        const char *name, const char *kind, Qnn_Tensor_t *in, uint32_t count,
        Qnn_Tensor_t *out, Qnn_Param_t *params, uint32_t nparams)
{
    char param_names[nparams ? nparams : 1][128];
    for (uint32_t i=0;i<nparams;++i) {
        if (params[i].paramType!=QNN_PARAMTYPE_TENSOR) continue;
        snprintf(param_names[i],sizeof(param_names[i]),"%s_%s",name,params[i].name);
        params[i].tensorParam.v1.name=param_names[i];
        Qnn_ErrorHandle_t e=api->tensorCreateGraphTensor(graph,&params[i].tensorParam);
        if(e)return e;
    }
    Qnn_OpConfig_t op=QNN_OPCONFIG_INIT;
    op.v1.name=name;op.v1.packageName="qti.aisw";op.v1.typeName=kind;
    op.v1.numOfInputs=count;op.v1.inputTensors=in;
    op.v1.numOfOutputs=1;op.v1.outputTensors=out;
    op.v1.numOfParams=nparams;op.v1.params=nparams?params:NULL;
    Qnn_ErrorHandle_t result=api->graphAddNode(graph,op);
    if(result)fprintf(stderr,"Add %s (%s): %llu\n",name,kind,(unsigned long long)result);
    return result;
}

#include FSR_GRAPH_HEADER

static void logger(const char *format, QnnLog_Level_t level, uint64_t stamp, va_list args)
{
    (void)level;(void)stamp;vfprintf(stderr,format,args);fputc('\n',stderr);
}

static size_t tensor_bytes(const Qnn_Tensor_t *t)
{
    size_t n=t->v1.dataType==QNN_DATATYPE_FLOAT_16?2:1;
    for(uint32_t i=0;i<t->v1.rank;++i)n*=t->v1.dimensions[i];
    return n;
}

#ifndef FSR4_NO_MAIN
int main(int argc,char **argv)
{
    if(argc!=3){fprintf(stderr,"Usage: run-graph INPUT_NHWC_U8 OUTPUT_NHWC_F16\n");return 2;}
    int status=1;
    void *lib=dlopen("libQnnHtp.so",RTLD_NOW|RTLD_LOCAL);
    if(!lib){fprintf(stderr,"%s\n",dlerror());return 1;}
    Qnn_ErrorHandle_t (*get)(const QnnInterface_t ***,uint32_t *)=dlsym(lib,"QnnInterface_getProviders");
    const QnnInterface_t **providers=NULL;uint32_t count=0;
    QNN_INTERFACE_VER_TYPE api={0};
    Qnn_LogHandle_t log=NULL;Qnn_BackendHandle_t backend=NULL;
    Qnn_DeviceHandle_t device=NULL;Qnn_ContextHandle_t context=NULL;Qnn_GraphHandle_t graph=NULL;
    void *input_data=NULL,*output_data=NULL;
    if(!get||get(&providers,&count))goto cleanup;
    for(uint32_t i=0;i<count;++i)
        if(providers[i]->apiVersion.coreApiVersion.major==QNN_API_VERSION_MAJOR &&
           providers[i]->apiVersion.coreApiVersion.minor>=QNN_API_VERSION_MINOR){api=providers[i]->QNN_INTERFACE_VER_NAME;break;}
    if(!api.backendCreate)goto cleanup;
#define STEP(call) do { Qnn_ErrorHandle_t e=(call);printf("%s: 0x%llx\n",#call,(unsigned long long)e);fflush(stdout);if(e)goto cleanup; } while(0)
    STEP(api.logCreate(logger,QNN_LOG_LEVEL_WARN,&log));
    STEP(api.backendCreate(log,NULL,&backend));
    STEP(api.deviceCreate(log,NULL,&device));
    STEP(api.contextCreate(backend,device,NULL,&context));
    QnnHtpGraph_CustomConfig_t htp_config=QNN_HTP_GRAPH_CUSTOM_CONFIG_INIT;
    QnnGraph_Config_t graph_config=QNN_GRAPH_CONFIG_INIT;
    const QnnGraph_Config_t *graph_configs[]={&graph_config,NULL};
    const char *optimization=getenv("FSR4_QNN_OPT");
    if(optimization){
        htp_config.option=QNN_HTP_GRAPH_CONFIG_OPTION_OPTIMIZATION;
        htp_config.optimizationOption.type=QNN_HTP_GRAPH_OPTIMIZATION_TYPE_FINALIZE_OPTIMIZATION_FLAG;
        htp_config.optimizationOption.floatValue=strtof(optimization,NULL);
        graph_config.option=QNN_GRAPH_CONFIG_OPTION_CUSTOM;
        graph_config.customConfig=&htp_config;
    }
    STEP(api.graphCreate(context,"fsr4_neural_core",optimization?graph_configs:NULL,&graph));
    Qnn_Tensor_t input=QNN_TENSOR_INIT,output=QNN_TENSOR_INIT;
    if(compose_fsr(&api,graph,&input,&output))goto cleanup;
    STEP(api.graphFinalize(graph,NULL,NULL));
    size_t input_size=tensor_bytes(&input),output_size=tensor_bytes(&output);
    input_data=malloc(input_size);output_data=calloc(1,output_size);
    if(!input_data||!output_data)goto cleanup;
    FILE *f=fopen(argv[1],"rb");if(!f){perror("input");goto cleanup;}
    size_t n=fread(input_data,1,input_size,f);int extra=fgetc(f);fclose(f);
    if(n!=input_size||extra!=EOF){fprintf(stderr,"Input size mismatch\n");goto cleanup;}
    input.v1.clientBuf=(Qnn_ClientBuffer_t){input_data,input_size};
    output.v1.clientBuf=(Qnn_ClientBuffer_t){output_data,output_size};
    /* FSR4_REPEAT=N runs N times on the same input and reports outputs that differ from the first. */
    const char *repeat=getenv("FSR4_REPEAT");unsigned runs=repeat?(unsigned)strtoul(repeat,NULL,10):3;if(runs<1)runs=1;
    unsigned char *first=malloc(output_size);if(!first)goto cleanup;
    for(unsigned i=0;i<runs;++i){
        struct timespec a,b;clock_gettime(CLOCK_MONOTONIC,&a);
        STEP(api.graphExecute(graph,&input,1,&output,1,NULL,NULL));
        clock_gettime(CLOCK_MONOTONIC,&b);
        size_t differ=0,lo=output_size,hi=0;
        if(i==0)memcpy(first,output_data,output_size);
        else for(size_t k=0;k<output_size;++k)if(first[k]!=((unsigned char*)output_data)[k]){++differ;if(k<lo)lo=k;hi=k;}
        printf("Neural execution %u: %.3f ms",i,(b.tv_sec-a.tv_sec)*1000.+(b.tv_nsec-a.tv_nsec)/1e6);
        if(i&&differ)printf(" DIFFERS from run 0: %zu bytes, offsets %zu..%zu",differ,lo,hi);
        printf("\n");
    }
    free(first);
    f=fopen(argv[2],"wb");if(!f){perror("output");goto cleanup;}
    n=fwrite(output_data,1,output_size,f);int closed=fclose(f);
    if(n!=output_size||closed)goto cleanup;
    printf("Wrote %zu bytes of neural parameters (not an RGB image)\n",output_size);
    status=0;
cleanup:
    if(context)api.contextFree(context,NULL);
    if(device)api.deviceFree(device);
    if(backend)api.backendFree(backend);
    if(log)api.logFree(log);
    free(input_data);free(output_data);dlclose(lib);
    return status;
}
#endif
