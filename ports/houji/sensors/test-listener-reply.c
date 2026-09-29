/* A valid optional RPC must get an error reply without stopping sensor service. */
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libhexagonrpc/hexagonrpc.h>
#include <libhexagonrpc/error.h>
#include "listener.h"
static unsigned calls;
int hexagonrpc(const struct hrpc_method_def_interp4 *def, int fd, uint32_t handle, ...)
{
    (void)fd;(void)handle;
    if (def->msg_id==3) return 0;
    assert(def->msg_id==4);
    va_list ap;va_start(ap,handle);
    (void)va_arg(ap,uint32_t);
    uint32_t result=va_arg(ap,uint32_t), length=va_arg(ap,uint32_t);
    const void *reply=va_arg(ap,const void *);
    uint32_t *rctx=va_arg(ap,uint32_t *),*iface=va_arg(ap,uint32_t *),*sc=va_arg(ap,uint32_t *),*input_length=va_arg(ap,uint32_t *);
    uint32_t capacity=va_arg(ap,uint32_t);void *input=va_arg(ap,void *);va_end(ap);
    assert(capacity>=512);
    if(calls==1)assert(result==AEE_EUNSUPPORTED && length==0);
    if(calls==2) {
        assert(result==0 && length==12);
        uint32_t value;memcpy(&value,(const char *)reply+8,4);assert(value==42);
        calls++;return -ECANCELED;
    }
    uint32_t words[3]={4,0,calls==0?36:41};
    memcpy(input,words,sizeof(words));*input_length=sizeof(words);
    *rctx=calls+1;*iface=0;*sc=REMOTE_SCALARS_MAKE(calls==0?31:0,1,1);
    calls++;return 0;
}
static uint32_t reply(void *data,const struct fastrpc_io_buffer *in,struct fastrpc_io_buffer *out)
{(void)data;*(uint32_t *)out[0].p=*(uint32_t *)in[0].p+1;return 0;}
int main(void)
{
    struct hrpc_arg_def_interp4 args[]={{HRPC_ARG_WORD,4},{HRPC_ARG_OUT_BLOB,4}};
    struct hrpc_method_def_interp4 def={.msg_id=0,.n_args=2,.args=args};
    struct fastrpc_function_impl impl={.def=&def,.impl=reply};
    struct fastrpc_interface iface={.name="test",.n_procs=1,.procs=&impl};
    struct fastrpc_interface *ifaces[]={&iface};
    assert(run_fastrpc_listener(-1,1,ifaces)==-ECANCELED && calls==3);
    puts("Unsupported RPC replied correctly; following RPC completed");
}
