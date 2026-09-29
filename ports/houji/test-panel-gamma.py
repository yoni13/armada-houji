from pathlib import Path
import subprocess,re
patch=(Path(__file__).resolve().parent/'patches/0001-drm-panel-xiaomi-n3.patch').read_text();s='\n'.join(line[1:] for line in patch.split('@@ -0,0 +1,',1)[1].splitlines()[1:] if line.startswith('+')); fn=s[s.index('static const u8 *n3_bootloader_gamma'):s.index('static int n3_on(')]
prefix='''#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef uint8_t u8;
struct device {int unused;};
#define QCOM_SMEM_HOST_ANY 0
#define IS_ERR(p) ((uintptr_t)(p) > (uintptr_t)-4096)
#define PTR_ERR(p) ((long)(p))
#define dev_info(...) ((void)0)
#define dev_warn(...) ((void)0)
static u8 buf[168]; static size_t test_size; static int missing;
static void *qcom_smem_get(int host, int item, size_t *size) {
 assert(host==0 && item==499); *size=test_size;return missing ? (void *)-2L : buf;
}
'''
tests='''int main(void) {
 struct device dev={0};
 for (unsigned c=0;c<3;c++) for(unsigned r=0;r<4;r++)buf[c*54+r*17]=0xb0+c*4+r;
 for(unsigned c=0;c<3;c++){buf[c*54+52]=1;buf[c*54+53]=7;}
 test_size=162;assert(n3_bootloader_gamma(&dev)==buf);
 test_size=168;assert(n3_bootloader_gamma(&dev)==buf);
 test_size=161;assert(!n3_bootloader_gamma(&dev));
 test_size=162;buf[71]=0;assert(!n3_bootloader_gamma(&dev));buf[71]=0xb5;
 buf[106]=buf[107]=0;assert(!n3_bootloader_gamma(&dev));
 memset(buf,0xff,sizeof(buf));assert(!n3_bootloader_gamma(&dev));
 missing=1;assert(!n3_bootloader_gamma(&dev));
 puts("Gamma tests passed: valid/padded, truncated, register mismatch, blank channel, all-ones and missing SMEM");
}
'''
import tempfile
workspace=tempfile.TemporaryDirectory(prefix='houji-gamma-test-');p=Path(workspace.name);(p/'test-gamma.c').write_text(prefix+fn+tests)
subprocess.run(['cc','-fsanitize=address,undefined','-Wall','-Wextra','-Wno-unused-parameter',str(p/'test-gamma.c'),'-o',str(p/'test-gamma')],check=True);subprocess.run([str(p/'test-gamma')],check=True)
