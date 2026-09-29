#!/usr/bin/env python3
"""Replay the phone's DBS/SBS record through the patched C parser on the host.

Pass an unpatched Linux 7.2.3 source directory. Only two files are copied to a
temporary directory; the kernel worktree is never modified by this check.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('kernel_source', type=Path)
args = parser.parse_args()


def function(text, name):
    match = re.search(r'static int\s+' + name + r'\(', text)
    if not match:
        raise ValueError(f'missing C function: {name}')
    start = text.index('{', match.end())
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


with tempfile.TemporaryDirectory(prefix='houji-wmi-parser-') as tmp:
    tmp = Path(tmp)
    relative = Path('drivers/net/wireless/ath/ath12k')
    (tmp / relative).mkdir(parents=True)
    for name in ('wmi.c', 'wmi.h'):
        shutil.copy2(args.kernel_source / relative / name, tmp / relative / name)
    with (HERE / 'patches/0002-ath12k-parse-nested-dbs-sbs.patch').open() as patch:
        subprocess.run(['patch', '-p1', '--batch', '--forward', '-F0'],
                       cwd=tmp, stdin=patch, check=True)
    source = (tmp / relative / 'wmi.c').read_text()
    header = (tmp / relative / 'wmi.h').read_text()
    tag = int(re.search(r'WMI_TAG_DBS_OR_SBS_CAP_EXT\s*=\s*(0x[0-9a-fA-F]+)', header)[1], 16)
    assert tag == 1024
    # The harness replaces only kernel types, logging, and the unrelated TLV
    # policy table. Both iteration and record decoding execute the real C.
    harness = r'''
#include <assert.h>
#include <endian.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
typedef uint16_t u16;
typedef uint32_t __le32;
struct wmi_tlv { __le32 header; };
struct ath12k_base { struct { unsigned preferred_hw_mode, sbs_lower_band_end_freq; } wmi_ab; };
struct ath12k_wmi_dbs_or_sbs_cap_params { __le32 hw_mode_id, sbs_lower_band_end_freq; };
static const struct { size_t min_len; } ath12k_wmi_tlv_policies[] = {{0}};
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define WMI_TLV_TAG 0xffff0000u
#define WMI_TLV_LEN 0x0000ffffu
#define le32_to_cpu(x) le32toh(x)
#define le32_get_bits(x, mask) ((le32toh(x) & (mask)) >> __builtin_ctz(mask))
#define ath12k_err(...) ((void)0)
#define ath12k_dbg(...) ((void)0)
#define WMI_TAG_DBS_OR_SBS_CAP_EXT 1024
'''
    harness += function(source, 'ath12k_wmi_tlv_iter') + '\n'
    harness += function(source, 'ath12k_wmi_tlv_dbs_or_sbs_caps') + '\n'
    harness += r'''
static void check(const void *p, size_t n, unsigned mode, int status, unsigned cutoff)
{
    struct ath12k_base ab = {.wmi_ab = {mode, 0}};
    assert(ath12k_wmi_tlv_iter(&ab, p, n, ath12k_wmi_tlv_dbs_or_sbs_caps, NULL) == status);
    assert(ab.wmi_ab.sbs_lower_band_end_freq == cutoff);
}
int main(void)
{
    /* Exact record captured from the phone, including its nested TLV header. */
    const unsigned char captured[] = {8,0,0,4,5,0,0,0,0x18,0x15,0,0};
    const unsigned char records[] = {
        4,0,1,4,0,0,0,0, /* unrelated tag */
        8,0,0,4,2,0,0,0,0x88,0x13,0,0, /* another mode, 5000 MHz */
        8,0,0,4,5,0,0,0,0x18,0x15,0,0};
    const unsigned char short_payload[] = {4,0,0,4,5,0,0,0};
    check(captured, sizeof(captured), 5, 0, 5400);
    check(captured, sizeof(captured), 2, 0, 0);
    check(records, sizeof(records), 5, 0, 5400);
    check(records, sizeof(records), 2, 0, 5000);
    check(captured, 0, 5, 0, 0);
    check(captured, 3, 5, -EINVAL, 0);
    check(captured, sizeof(captured)-1, 5, -EINVAL, 0);
    check(short_payload, sizeof(short_payload), 5, -EINVAL, 0);
    puts("PASS: captured SBS record, mode selection, unrelated tag, empty array and truncated input");
}
'''
    (tmp / 'replay.c').write_text(harness)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=undefined',
                    str(tmp / 'replay.c'), '-o', str(tmp / 'replay')], check=True)
    subprocess.run([str(tmp / 'replay')], check=True)
