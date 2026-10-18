#!/usr/bin/env python3
"""Exercise the actual patched governor with latency and state-list cases."""
from pathlib import Path
import subprocess
import tempfile
import unittest

PORT=Path(__file__).resolve().parents[1]


class Guard(unittest.TestCase):
    def test_selected_psci_state_and_constraint_rejection(self):
        patch=(PORT/'patches/0033-cpuidle-psci-houji-avoid-apss-collapse-in-s2idle.patch').read_text()
        code='\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        start=code.index('static bool houji_s2idle_power_down_ok')
        end=code.index('\n}',start)+2
        function=code[start:end]
        harness=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef uint32_t u32;
struct dev_pm_domain { int unused; };
struct state { u32 *data; };
struct generic_pm_domain { struct dev_pm_domain domain; struct state *states; unsigned state_idx; };
#define pd_to_genpd(d) ((struct generic_pm_domain *)(d))
#define PM_SUSPEND_TO_IDLE 1
static int pm_suspend_target_state = 1;
static bool allow;
static unsigned selected;
static bool original(struct dev_pm_domain *domain) {
    if (!allow) return false;
    pd_to_genpd(domain)->state_idx = selected;
    return true;
}
static struct { bool (*system_power_down_ok)(struct dev_pm_domain *); } pm_domain_cpu_gov = { original };
FUNCTION
int main(void) {
    u32 cpu=0x40000004, cluster=0x41000044, apss=0x4100c344;
    struct state states[] = {{&cluster},{&apss}};
    struct generic_pm_domain pd = {.states=states,.state_idx=1};
    allow=true;selected=1;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==0);
    /* A latency veto must not be turned into permission by the cap. */
    allow=false;pd.state_idx=1;
    assert(!houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==1);
    allow=true;selected=0;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==0);
    pm_suspend_target_state=3;selected=1;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==1);
    pm_suspend_target_state=1;states[0].data=&cpu;
    assert(!houji_s2idle_power_down_ok(&pd.domain));
    selected=0;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==0);
    states[0].data=NULL;
    assert(houji_s2idle_power_down_ok(&pd.domain));
    /* Search only shallower, DT-described states, not a hard-coded index. */
    struct state longer[]={{&cpu},{&cluster},{&apss}};
    pd.states=longer;selected=2;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==1);
    longer[2].data=&cluster;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==2);
    longer[2].data=NULL;
    assert(houji_s2idle_power_down_ok(&pd.domain) && pd.state_idx==2);
}
'''.replace('FUNCTION',function)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'guard.c';binary=Path(directory)/'guard'
            source.write_text(harness)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)


if __name__=='__main__':unittest.main()
