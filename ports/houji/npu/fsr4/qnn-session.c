/* Reusable native QNN session for the standalone Vulkan pipeline. */
#define FSR4_NO_MAIN
#include "run-graph.c"
#include <limits.h>
#include "HTP/QnnHtpDevice.h"
#include "experimental-gate.h"

struct FsrQnn {
    void *library, *rpc;
    QNN_INTERFACE_VER_TYPE api;
    Qnn_LogHandle_t log;
    Qnn_BackendHandle_t backend;
    Qnn_DeviceHandle_t device;
    Qnn_ContextHandle_t context;
    Qnn_GraphHandle_t graph;
    Qnn_Tensor_t tensors[2];
    Qnn_MemHandle_t handles[2];
    unsigned registered;
    void *buffers[2];
    size_t sizes[2];
    int fds[2];
    void *(*alloc)(int,uint32_t,int);
    void (*free_buffer)(void *);
    int (*to_fd)(void *);
    QnnHtpDevice_PerfInfrastructure_t *perf;
    uint32_t power_id;
    int power_created;
};

void fsr_qnn_free(struct FsrQnn *s)
{
    if(!s)return;
    if(s->registered)s->api.memDeRegister(s->handles,s->registered);
    if(s->context)s->api.contextFree(s->context,NULL);
    if(s->power_created)s->perf->destroyPowerConfigId(s->power_id);
    if(s->device)s->api.deviceFree(s->device);
    if(s->backend)s->api.backendFree(s->backend);
    if(s->log)s->api.logFree(s->log);
    for(unsigned i=0;i<2;++i)if(s->buffers[i]&&s->free_buffer)s->free_buffer(s->buffers[i]);
    if(s->rpc)dlclose(s->rpc);
    if(s->library)dlclose(s->library);
    free(s);
}

struct FsrQnn *fsr_qnn_create(void)
{
    if(!houji_fsr4_experimental_enabled())return NULL;
    struct FsrQnn *s=calloc(1,sizeof(*s));
    if(!s)return NULL;
    s->library=dlopen("libQnnHtp.so",RTLD_NOW|RTLD_LOCAL);
    if(!s->library){fprintf(stderr,"%s\n",dlerror());goto fail;}
    Qnn_ErrorHandle_t (*get)(const QnnInterface_t ***,uint32_t *)=dlsym(s->library,"QnnInterface_getProviders");
    const QnnInterface_t **providers=NULL;uint32_t count=0;
    if(!get||get(&providers,&count))goto fail;
    for(uint32_t i=0;i<count;++i)
        if(providers[i]->apiVersion.coreApiVersion.major==QNN_API_VERSION_MAJOR&&providers[i]->apiVersion.coreApiVersion.minor>=QNN_API_VERSION_MINOR){s->api=providers[i]->QNN_INTERFACE_VER_NAME;break;}
    if(!s->api.backendCreate)goto fail;
#define SESSION(call) do { Qnn_ErrorHandle_t e=(call);if(e){fprintf(stderr,"%s: 0x%llx\n",#call,(unsigned long long)e);goto fail;} } while(0)
    SESSION(s->api.logCreate(logger,QNN_LOG_LEVEL_WARN,&s->log));
    SESSION(s->api.backendCreate(s->log,NULL,&s->backend));
    SESSION(s->api.deviceCreate(s->log,NULL,&s->device));
    if(getenv("FSR4_HTP_PERFORMANCE")) {
        QnnDevice_Infrastructure_t infrastructure=NULL;
        SESSION(s->api.deviceGetInfrastructure(&infrastructure));
        QnnHtpDevice_Infrastructure_t *htp=(QnnHtpDevice_Infrastructure_t *)infrastructure;
        if(!htp||htp->infraType!=QNN_HTP_DEVICE_INFRASTRUCTURE_TYPE_PERF)goto fail;
        s->perf=&htp->perfInfra;
        SESSION(s->perf->createPowerConfigId(0,0,&s->power_id));s->power_created=1;
        QnnHtpPerfInfrastructure_PowerConfig_t power={0};
        power.option=QNN_HTP_PERF_INFRASTRUCTURE_POWER_CONFIGOPTION_DCVS_V3;
        power.dcvsV3Config.contextId=s->power_id;
        power.dcvsV3Config.setDcvsEnable=1;power.dcvsV3Config.dcvsEnable=1;
        power.dcvsV3Config.powerMode=QNN_HTP_PERF_INFRASTRUCTURE_POWERMODE_PERFORMANCE_MODE;
        power.dcvsV3Config.setSleepLatency=1;power.dcvsV3Config.sleepLatency=100;
        const char *corner=getenv("FSR4_HTP_CORNER");
        if(corner){
            QnnHtpPerfInfrastructure_VoltageCorner_t target;
            if(strcmp(corner,"svs2")==0)target=DCVS_VOLTAGE_VCORNER_SVS2;
            else if(strcmp(corner,"svs")==0)target=DCVS_VOLTAGE_VCORNER_SVS;
            else if(strcmp(corner,"svs_plus")==0)target=DCVS_VOLTAGE_VCORNER_SVS_PLUS;
            else if(strcmp(corner,"nominal")==0)target=DCVS_VOLTAGE_VCORNER_NOM;
            else if(strcmp(corner,"turbo")==0)target=DCVS_VOLTAGE_VCORNER_TURBO;
            else{fprintf(stderr,"FSR4_HTP_CORNER must be svs2, svs, svs_plus, nominal or turbo\n");goto fail;}
            power.dcvsV3Config.setBusParams=1;power.dcvsV3Config.setCoreParams=1;
            power.dcvsV3Config.busVoltageCornerMin=target;
            power.dcvsV3Config.busVoltageCornerTarget=target;
            power.dcvsV3Config.busVoltageCornerMax=target;
            power.dcvsV3Config.coreVoltageCornerMin=target;
            power.dcvsV3Config.coreVoltageCornerTarget=target;
            power.dcvsV3Config.coreVoltageCornerMax=target;
        }
        const QnnHtpPerfInfrastructure_PowerConfig_t *powers[]={&power,NULL};
        SESSION(s->perf->setPowerConfig(s->power_id,powers));
        printf("HTP performance mode enabled for this session; DCVS and sleep remain enabled\n");
    }
    SESSION(s->api.contextCreate(s->backend,s->device,NULL,&s->context));
    QnnHtpGraph_CustomConfig_t htp=QNN_HTP_GRAPH_CUSTOM_CONFIG_INIT;
    htp.option=QNN_HTP_GRAPH_CONFIG_OPTION_OPTIMIZATION;
    htp.optimizationOption.type=QNN_HTP_GRAPH_OPTIMIZATION_TYPE_FINALIZE_OPTIMIZATION_FLAG;
    htp.optimizationOption.floatValue=1.0f;
    const char *optimization=getenv("FSR4_QNN_OPT");
    if(optimization)htp.optimizationOption.floatValue=strtof(optimization,NULL);
    QnnGraph_Config_t config=QNN_GRAPH_CONFIG_INIT;config.option=QNN_GRAPH_CONFIG_OPTION_CUSTOM;config.customConfig=&htp;
    const QnnGraph_Config_t *configs[]={&config,NULL};
    SESSION(s->api.graphCreate(s->context,"fsr4_neural_core",configs,&s->graph));
    if(compose_fsr(&s->api,s->graph,&s->tensors[0],&s->tensors[1]))goto fail;
    SESSION(s->api.graphFinalize(s->graph,NULL,NULL));
    s->rpc=dlopen("libcdsprpc.so",RTLD_NOW|RTLD_LOCAL);if(!s->rpc)goto fail;
    s->alloc=dlsym(s->rpc,"rpcmem_alloc");s->free_buffer=dlsym(s->rpc,"rpcmem_free");s->to_fd=dlsym(s->rpc,"rpcmem_to_fd");
    if(!s->alloc||!s->free_buffer||!s->to_fd)goto fail;
    for(unsigned i=0;i<2;++i){
        s->sizes[i]=tensor_bytes(&s->tensors[i]);
        if(s->sizes[i]>INT_MAX-65536)goto fail;
        int allocated=(int)((s->sizes[i]+65535)&~(size_t)65535);
        /* FSR4_RPCMEM_FLAGS: 1 = cached (RPCMEM_DEFAULT_FLAGS), 0 = uncached. Only
         * the GPU and NPU touch these buffers in the frame server. */
        const char *flags=getenv("FSR4_RPCMEM_FLAGS");
        s->buffers[i]=s->alloc(25,flags?(uint32_t)strtoul(flags,NULL,0):1,allocated);if(!s->buffers[i])goto fail;
        s->fds[i]=s->to_fd(s->buffers[i]);if(s->fds[i]<0)goto fail;
        Qnn_MemDescriptor_t desc=QNN_MEM_DESCRIPTOR_INIT;
        desc.memShape.numDim=s->tensors[i].v1.rank;desc.memShape.dimSize=s->tensors[i].v1.dimensions;
        desc.dataType=s->tensors[i].v1.dataType;desc.memType=QNN_MEM_TYPE_ION;desc.ionInfo.fd=s->fds[i];
        SESSION(s->api.memRegister(s->context,&desc,1,&s->handles[i]));++s->registered;
        s->tensors[i].v1.memType=QNN_TENSORMEMTYPE_MEMHANDLE;s->tensors[i].v1.memHandle=s->handles[i];
    }
    return s;
fail:
    fsr_qnn_free(s);return NULL;
}

int fsr_qnn_fd(struct FsrQnn *s,unsigned i){return i<2?s->fds[i]:-1;}
void *fsr_qnn_pointer(struct FsrQnn *s,unsigned i){return i<2?s->buffers[i]:NULL;}
size_t fsr_qnn_size(struct FsrQnn *s,unsigned i){return i<2?s->sizes[i]:0;}
int fsr_qnn_execute(struct FsrQnn *s)
{
    Qnn_ErrorHandle_t e=s->api.graphExecute(s->graph,&s->tensors[0],1,&s->tensors[1],1,NULL,NULL);
    if(e)fprintf(stderr,"QNN execute: %llu\n",(unsigned long long)e);
    return e?1:0;
}
