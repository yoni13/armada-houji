/* Compile with the patched uMTP usb_gadget.c and --gc-sections. */
#include "buildconf.h"
#include <assert.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <sys/types.h>
#include <endian.h>
#include "fs_handles_db.h"
#include "mtp.h"
#include "usb_gadget.h"
#include "usb_gadget_fct.h"

void fill_ep_descriptor(mtp_ctx *, usb_gadget *,
                        struct usb_endpoint_descriptor_no_audio *, int, unsigned int);

int main(void)
{
    ep_cfg_descriptor desc;
    fill_ep_descriptor(NULL, NULL, &desc.ep_desc, 1, EP_BULK_MODE | EP_IN_DIR);
    assert(le16toh(desc.ep_desc.wMaxPacketSize) == 64);
    fill_ep_descriptor(NULL, NULL, &desc.ep_desc, 1, EP_BULK_MODE | EP_IN_DIR | EP_HS_MODE);
    assert(le16toh(desc.ep_desc.wMaxPacketSize) == 512);
    fill_ep_descriptor(NULL, NULL, &desc.ep_desc, 1, EP_BULK_MODE | EP_IN_DIR | EP_SS_MODE);
    assert(le16toh(desc.ep_desc.wMaxPacketSize) == 1024);
    assert(desc.ep_desc_comp.bLength == 6);
    assert(desc.ep_desc_comp.bDescriptorType == USB_DT_SS_ENDPOINT_COMP);
    assert(desc.ep_desc_comp.wBytesPerInterval == 0);
    fill_ep_descriptor(NULL, NULL, &desc.ep_desc, 3, EP_INT_MODE | EP_IN_DIR | EP_SS_MODE);
    assert(le16toh(desc.ep_desc_comp.wBytesPerInterval) == 28);
    assert(desc.ep_desc_comp.bMaxBurst == 0);
    puts("MTP FS/HS/SS descriptors passed");
}
