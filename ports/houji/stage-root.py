#!/usr/bin/env python3
"""Populate a freshly created Armada OCI container; never reads a handset."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from buildlib import PORT, REPO, kernel_source, run
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('root',type=Path)
p.add_argument('work',type=Path)
a=p.parse_args(); root=a.root.resolve();work=a.work.resolve();users=work/'userspace'
release=(kernel_source(work)/'include/config/kernel.release').read_text().strip()
# The pinned Armada base provides these through python3-gobject and zenity.
# Fail clearly when an alternative OCI image omits the NFC application's UI.
for name in ['Gtk-4.0','Adw-1']:
    if not (root/'usr/lib64/girepository-1.0'/(name+'.typelib')).is_file():
        raise ValueError('NFC Manager needs GTK4/libadwaita in the Armada base: '+name)

def target(name):
    path=root/name.lstrip('/')
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.parent.resolve().is_relative_to(root):
        raise ValueError('Target path escapes OCI root: '+name)
    return path

def copy(src,name,mode=None):
    dst=target(name)
    dst.unlink(missing_ok=True)
    shutil.copyfile(src,dst)
    dst.chmod(mode or (0o755 if Path(src).read_bytes()[:2]==b'#!' else 0o644))

def write(name,text,mode=0o644):
    dst=target(name);dst.unlink(missing_ok=True);dst.write_text(text);dst.chmod(mode)

def link(name,value):
    dst=target(name);dst.unlink(missing_ok=True);dst.symlink_to(value)

for directory in [PORT/'runtime',PORT/'firmware/root']:
    for src in directory.rglob('*'):
        # Tests that import scripts from here must not leak bytecode into images.
        if src.is_file() and '__pycache__' not in src.parts:copy(src,str(src.relative_to(directory)))
for name in ['armada-powerd','device-env','fake-suspend','mtp-gadget']:
    rel='usr/libexec/armada/'+name;copy(REPO/'system_files'/rel,rel,0o755)
for name in ['defaults.conf','xiaomi-14.conf']:
    rel='usr/lib/armada/devices/'+name;copy(REPO/'system_files'/rel,rel)
# Native suspend lets NetworkManager disconnect before the radio powers down.
# Armada's sleep-mode switch recreates this marker if the owner selects light sleep.
target('etc/NetworkManager/ignore-sleep').unlink(missing_ok=True)
for src in (REPO/'system_files/usr/share/inputplumber').rglob('*shanwan*'):
    if src.is_file():copy(src,str(src.relative_to(REPO/'system_files')))
# Base kernels cannot load modules built for this kernel release.
modules=target('usr/lib/modules')
shutil.rmtree(modules)
shutil.copytree(work/('kernel/staging-'+release)/'lib/modules',modules,symlinks=True)
copy(work/'touch-module/houji-tcm-probe.ko','usr/lib/modules/'+release+'/extra/houji-tcm-probe.ko')
copy(work/'nfc-module/houji-nfc-power.ko','usr/lib/modules/'+release+'/extra/houji-nfc-power.ko')
for linkpath in (modules/release).glob('*'):
    if linkpath.name in ['build','source'] and linkpath.is_symlink():linkpath.unlink()
run('depmod','-b',root,release)
for name in ['houji-stock-core','houji-stock-auth']:
    copy(users/'bin'/name,'usr/libexec/armada/'+name,0o755)
for name in ['qbootctl','umtprd']:
    copy(users/'bin'/name,'usr/bin/'+name,0o755)
# NFC stays off until explicitly enabled through neard's D-Bus interface.
for name in ['etc/dbus-1/system.d/org.neard.conf',
             'usr/lib/systemd/system/neard.service',
             'usr/share/licenses/neard/COPYING',
             'usr/share/man/man5/neard.conf.5.gz',
             'usr/share/man/man8/neard.8.gz']:
    copy(users/'neard'/name,name)
copy(users/'neard/usr/libexec/nfc/neard','usr/libexec/nfc/neard',0o755)
for name in ['gui.py','service.py','controller.py','transport.py','protocol.py']:
    copy(PORT/'nfc'/name,'usr/libexec/armada/nfc-manager/'+name)
copy(users/'bin/Xiaomi-14-tplg.bin','usr/lib/firmware/qcom/houji/Xiaomi-14-tplg.bin')
for name,path in {'ssccli':'libssc-build/src/ssccli', 'hexagonrpcd':'hexagon-build/hexagonrpcd/hexagonrpcd',
                  'monitor-sensor':'iio-build/src/monitor-sensor'}.items():
    copy(users/'sensors'/path,'usr/bin/'+name,0o755)
copy(users/'sensors/iio-build/src/iio-sensor-proxy','usr/libexec/iio-sensor-proxy',0o755)
for name,path in {'libssc.so.2':'libssc-build/src/libssc.so.2','libhexagonrpc.so.0.5':'hexagon-build/libhexagonrpc/libhexagonrpc.so.0.5'}.items():
    copy(users/'sensors'/path,'usr/lib64/'+name,0o755)
copy(users/'gamescope/gamescope','usr/bin/gamescope',0o755)
copy(users/'sensors/gamescope-build/houji-gamescope-rotate','usr/libexec/armada/houji-gamescope-rotate',0o755)
copy(PORT/'sensors/gamescope/orientation.py','usr/libexec/armada/houji-gamescope-orientation',0o755)
copy(PORT/'sensors/gamescope/49-houji-orientation.rules','etc/polkit-1/rules.d/49-houji-orientation.rules')
copy(PORT/'sensors/gamescope/houji-gamescope-orientation.service','etc/systemd/system/houji-gamescope-orientation.service')
copy(users/'sensors/iio-build/data/iio-sensor-proxy.service','etc/systemd/system/iio-sensor-proxy.service')
write('etc/udev/rules.d/90-houji-sensors.rules','SUBSYSTEM=="misc", KERNEL=="fastrpc-adsp", ENV{IIO_SENSOR_PROXY_TYPE}="ssc-accel", ENV{ACCEL_MOUNT_MATRIX}="-1,0,0;0,-1,0;0,0,1", TAG+="systemd", ENV{SYSTEMD_WANTS}+="iio-sensor-proxy.service"\n')
write('etc/modules-load.d/houji.conf','qcom-hv-haptics\nhci_uart\n')
write('etc/modprobe.d/houji-touch.conf','blacklist houji_tcm_probe\n')
write('etc/udev/rules.d/70-houji-haptics.rules','ACTION!="remove", SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="qcom-hv-haptics", TAG+="uaccess", ENV{FEEDBACKD_TYPE}="vibra"\n')
for src in ['charge-policy.py','board_thermal.py','stock_thermal.py']:
    copy(PORT/'charging'/src,'usr/libexec/armada/'+('houji-charge-policy' if src=='charge-policy.py' else src),0o755)
copy(PORT/'charging/houji-charging.service','etc/systemd/system/houji-charging.service')
copy(PORT/'charging/suspend.py','usr/libexec/armada/houji-suspend',0o755)
for name in ['armada-power-profiles','houji-wait-battery','houji-steam-power']:
    copy(PORT/'power'/name,'usr/libexec/armada/'+name,0o755)
for name in ['armada-power-profiles.service','houji-steam-power.service']:
    copy(PORT/'power'/name,'etc/systemd/system/'+name)
copy(PORT/'power/upower-houji.conf','etc/systemd/system/upower.service.d/20-houji-battery-ready.conf')
copy(PORT/'power/armada-power-profiles.conf','etc/dbus-1/system.d/armada-power-profiles.conf')
copy(PORT/'power/org.armada.power-profiles.policy','usr/share/polkit-1/actions/org.armada.power-profiles.policy')
copy(PORT/'audio/prepare-protection.py','usr/libexec/houji-audio/prepare-protection.py',0o755)
copy(PORT/'audio/ucm/Xiaomi-14.conf','usr/share/alsa/ucm2/conf.d/houji/Xiaomi-14.conf')
copy(PORT/'audio/ucm/HiFi.conf','usr/share/alsa/ucm2/Houji/HiFi.conf')
target('etc/sddm.conf.d/zz-holo-autologin.conf').unlink(missing_ok=True)
write('etc/sddm.conf.d/zz-steamos-autologin.conf','[Autologin]\nSession=armada-plasma-mobile.desktop\n')
(root/'etc/sudoers.d/houji-session-switch').chmod(0o440)
# This layout is managed by this port's installer, not bootc/ESP tools.
for name in ['udisks2','bootc-generic-growpart','armada-update-reserve','armada-session-default',
             'NetworkManager-wait-online','systemd-networkd','systemd-networkd-wait-online',
             'armada-bootimg-sync','armada-esp-rename','armada-installer-visibility']:
    link('etc/systemd/system/'+name+'.service','/dev/null')
link('etc/systemd/system/systemd-networkd.socket','/dev/null')
# The generic installer cannot recognize or install this userdata/overlay layout.
target('usr/share/applications/armada-installer.desktop').unlink(missing_ok=True)
# No remote-login listener is enabled in a fresh image. Owners can enable it later.
for name in ['sshd.service','sshd.socket']:
    for path in (root/'etc/systemd/system').glob('*.wants/'+name):path.unlink()
# Device firmware overrides must not be shadowed by another board's board-2.bin.
wifi=root/'usr/lib/firmware/ath12k/WCN7850/hw2.0'
for path in wifi.glob('board-2.bin*'):path.unlink()
for name in ['houji-stock-touch','houji-sensors','houji-gamescope-orientation','houji-grow-data',
             'houji-charging','armada-power-profiles','houji-steam-power','armada-nfc']:
    link('etc/systemd/system/multi-user.target.wants/'+name+'.service','../'+name+'.service')
link('etc/systemd/system/graphical.target.wants/houji-boot-success.service','../houji-boot-success.service')
link('etc/systemd/system/sleep.target.wants/houji-gamescope-sleep.service',
     '/usr/lib/systemd/system/houji-gamescope-sleep.service')
for name in ['bluetooth','NetworkManager']:
    link('etc/systemd/system/multi-user.target.wants/'+name+'.service','/usr/lib/systemd/system/'+name+'.service')
link('etc/systemd/system/dbus-org.bluez.service','/usr/lib/systemd/system/bluetooth.service')
# GPS remains opt-in. Nothing here enables the modem or a location logger.
for name in ['rmtfs','qrtr-lookup','tqftpserv','houji-loc-test']:
    copy(work/'gps'/name,'usr/libexec/houji-gps/'+name,0o755)
copy(PORT/'gps/nmea-bridge.py','usr/libexec/houji-gps/nmea-bridge.py',0o755)
copy(work/'gps/module/houji-modem-overlay.ko','usr/lib/modules/'+release+'/extra/houji-modem-overlay.ko')
run('depmod','-b',root,release)
print('Staged clean Houji userspace and modules:',release)
