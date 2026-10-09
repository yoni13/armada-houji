#!/usr/bin/env python3
"""Populate a freshly created Armada OCI container; never reads a handset."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from buildlib import PORT, REPO, kernel_source, run, stage_modules
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('root',type=Path)
p.add_argument('work',type=Path)
a=p.parse_args(); root=a.root.resolve();work=a.work.resolve();users=work/'userspace'
release=(kernel_source(work)/'include/config/kernel.release').read_text().strip()
run('python3',PORT/'cellular/runtime-packages.py','--work',work/'cellular','--root',root)
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
# armada-game-launch carries the FEX Vulkan thunk fix for native x86 games (Counter-Strike 2)
# until the pinned Armada image includes it.
for name in ['armada-powerd','device-env','fake-suspend','mtp-gadget','armada-game-launch']:
    rel='usr/libexec/armada/'+name;copy(REPO/'system_files'/rel,rel,0o755)
for name in ['defaults.conf','xiaomi-14.conf']:
    rel='usr/lib/armada/devices/'+name;copy(REPO/'system_files'/rel,rel)
# The device profile's cursor scale needs gamescope-session support; apply Armada's
# package patch until the pinned image ships it.
session_script=target('usr/share/gamescope-session-plus/gamescope-session-plus')
if 'ARMADA_GAMESCOPE_CURSOR_SCALE_HEIGHT' not in session_script.read_text():
    run('patch','-p1','-F0','--no-backup-if-mismatch','-d',root,
        '-i',REPO/'packages/gamescope-session/patches/0008-armada-cursor-scale-height.patch')
# Native suspend lets NetworkManager disconnect before the radio powers down.
# Armada's sleep-mode switch recreates this marker if the owner selects light sleep.
target('etc/NetworkManager/ignore-sleep').unlink(missing_ok=True)
for pattern in ('*shanwan*','*gamesir*'):
    for src in (REPO/'system_files/usr/share/inputplumber').rglob(pattern):
        if src.is_file():copy(src,str(src.relative_to(REPO/'system_files')))
# Keeps Decky's unpacked frontend out of /tmp ageing until the pinned image ships it.
copy(REPO/'system_files/usr/lib/tmpfiles.d/armada-decky.conf','usr/lib/tmpfiles.d/armada-decky.conf')
# Houji Settings: armada-decky-sync seeds it into Decky beside Armada's own plugins.
copy(PORT/'settings/houji-settings.py','usr/libexec/armada/houji-settings',0o755)
link('etc/systemd/system/sockets.target.wants/houji-settings.socket','/usr/lib/systemd/system/houji-settings.socket')
link('etc/systemd/system/multi-user.target.wants/houji-pstore-archive.service','/usr/lib/systemd/system/houji-pstore-archive.service')
for name in ['plugin.json','package.json','main.py','dist/index.js']:
    copy(work/'decky/houji-settings'/name,'usr/share/decky-plugins/houji-settings/'+name,0o644)
# Base kernels cannot load modules built for this kernel release.
stage_modules(work,release,target('usr/lib/modules'))
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
copy(users/'mangohud/mangoapp','usr/bin/mangoapp',0o755)
copy(users/'sensors/gamescope-build/houji-gamescope-rotate','usr/libexec/armada/houji-gamescope-rotate',0o755)
copy(PORT/'sensors/gamescope/orientation.py','usr/libexec/armada/houji-gamescope-orientation',0o755)
copy(PORT/'sensors/gamescope/49-houji-orientation.rules','etc/polkit-1/rules.d/49-houji-orientation.rules')
copy(PORT/'sensors/gamescope/houji-gamescope-orientation.service','etc/systemd/system/houji-gamescope-orientation.service')
copy(users/'sensors/iio-build/data/iio-sensor-proxy.service','etc/systemd/system/iio-sensor-proxy.service')
copy(users/'sensors/als-build/houji-als','usr/libexec/armada/houji-als',0o755)
copy(PORT/'sensors/als/houji-als.service','etc/systemd/system/houji-als.service')
write('etc/udev/rules.d/90-houji-sensors.rules','SUBSYSTEM=="misc", KERNEL=="fastrpc-adsp", ENV{IIO_SENSOR_PROXY_TYPE}="ssc-accel ssc-light", ENV{ACCEL_MOUNT_MATRIX}="-1,0,0;0,-1,0;0,0,1", TAG+="systemd", ENV{SYSTEMD_WANTS}+="iio-sensor-proxy.service"\n'
      # houji-als feeds the "als" IIO device and watches its reads; the proxy
      # reads the same light through SSC, so keep it off this device.
      'SUBSYSTEM=="iio", ATTR{name}=="als", ENV{IIO_SENSOR_PROXY_TYPE}=""\n')
write('etc/systemd/user/gamescope-session-plus@steam.service.d/20-houji-adaptive-brightness.conf',
      '[Service]\n# Steam offers adaptive brightness only with this set.\nEnvironment=STEAM_ENABLE_DYNAMIC_BACKLIGHT=1\n')
write('etc/systemd/user/gamescope-session-plus@steam.service.d/21-houji-mangohud.conf',
      '[Service]\n# Performance overlay levels without readings this phone cannot provide.\n'
      'Environment=MANGOHUD_PRESETSFILE=/usr/share/houji/mangohud-presets.conf\n')
# Plasma Mobile's built-in default only toggles the screen, leaving the phone
# awake. Sleep (1) matches Game Mode; users can still change it in Plasma.
write('etc/xdg/powerdevilrc', ''.join(
    f'[{profile}][SuspendAndShutdown]\nPowerButtonAction=1\n\n'
    for profile in ('AC', 'Battery', 'LowBattery')))
write('etc/modules-load.d/houji.conf','qcom-hv-haptics\nhci_uart\n')
write('etc/modprobe.d/houji-touch.conf','blacklist houji_tcm_probe\n')
write('etc/udev/rules.d/70-houji-haptics.rules','ACTION!="remove", SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="qcom-hv-haptics", TAG+="uaccess", ENV{FEEDBACKD_TYPE}="vibra"\n')
for src in ['charge-policy.py','board_thermal.py','stock_thermal.py']:
    copy(PORT/'charging'/src,'usr/libexec/armada/'+('houji-charge-policy' if src=='charge-policy.py' else src),0o755)
copy(PORT/'charging/houji-charging.service','etc/systemd/system/houji-charging.service')
copy(PORT/'thermal/houji-thermal','usr/libexec/armada/houji-thermal',0o755)
copy(PORT/'thermal/thermal_policy.py','usr/libexec/armada/thermal_policy.py')
copy(PORT/'thermal/houji-thermal.service','etc/systemd/system/houji-thermal.service')
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
for name in ['houji-stock-touch','houji-sensors','houji-als','houji-gamescope-orientation','houji-grow-data',
             'houji-charging','houji-thermal','armada-power-profiles','houji-steam-power','armada-nfc']:
    link('etc/systemd/system/multi-user.target.wants/'+name+'.service','../'+name+'.service')
link('etc/systemd/system/graphical.target.wants/houji-boot-success.service','../houji-boot-success.service')
link('etc/systemd/system/sleep.target.wants/houji-gamescope-sleep.service',
     '/usr/lib/systemd/system/houji-gamescope-sleep.service')
for name in ['bluetooth','NetworkManager']:
    link('etc/systemd/system/multi-user.target.wants/'+name+'.service','/usr/lib/systemd/system/'+name+'.service')
link('etc/systemd/system/dbus-org.bluez.service','/usr/lib/systemd/system/bluetooth.service')
# GPS remains opt-in; cellular startup below does not start a location logger.
for name in ['rmtfs','qrtr-lookup','tqftpserv','houji-loc-test']:
    copy(work/'gps'/name,'usr/libexec/houji-gps/'+name,0o755)
copy(PORT/'gps/nmea-bridge.py','usr/libexec/houji-gps/nmea-bridge.py',0o755)
# The modem is gated by verified factory-license access before it is started.
cell=work/'cellular/stage'
for name in ['prepare.py','qmi.py','license-relay.py','sim.py','service.py','wifi-priority.py','start-modem','run-supplicant']:
    copy(PORT/'cellular'/name,'usr/libexec/houji-cellular/'+name,0o755)
copy(PORT/'cellular/houji-cellular','usr/bin/houji-cellular',0o755)
for name in ['qtee_supplicant','lpac']:
    copy(cell/'bin'/name,'usr/libexec/houji-cellular/'+name,0o755)
copy(cell/'bin/ModemManager','usr/sbin/ModemManager',0o755)
copy(cell/'lib/libhouji-qtee.so','usr/libexec/houji-cellular/libhouji-qtee.so',0o755)
copy(cell/'lib/kcm_houji_sim.so','usr/lib64/qt6/plugins/plasma/kcms/systemsettings/kcm_houji_sim.so',0o755)
for name in ['libKF6ModemManagerQt.so.6', 'libplasmanm_cellular.so']:
    copy(cell/'lib'/name,'usr/lib64/'+name,0o755)
for name in ['minkadaptor','timeservice','fsservice','gpfsservice','taautoload','rpmbservice']:
    copy(cell/'lib'/('lib'+name+'.so.1'),'usr/libexec/houji-cellular/lib/lib'+name+'.so.1',0o755)
link('usr/lib/firmware/qcom/houji/modem','/run/houji/modemfw/image')
link('etc/systemd/system/multi-user.target.wants/houji-cellular.service',
     '/usr/lib/systemd/system/houji-cellular.service')
nm_plugins = root/'usr/lib64/NetworkManager/1.58.1-1.fc44.armada'
if not nm_plugins.is_dir():
    raise ValueError('Cellular WWAN plugin needs the pinned NetworkManager 1.58.1-1.fc44.armada base')
for name in ['libnm-wwan.so','libnm-device-plugin-wwan.so']:
    copy(cell/'lib'/name,'usr/lib64/NetworkManager/1.58.1-1.fc44.armada/'+name,0o755)
for source, filename in [('minkipc','LICENSE.txt'),('quic-teec','LICENSE.txt'),
                         ('QCBOR','LICENSE'),('NetworkManager','COPYING'),('ModemManager','COPYING')]:
    copy(work/'cellular/sources'/source/filename,'usr/share/licenses/houji-cellular/'+source+'-'+filename)
for src in (work/'cellular/sources/lpac/LICENSES').glob('*.txt'):
    copy(src,'usr/share/licenses/houji-cellular/lpac/'+src.name)
for source in ['modemmanager-qt', 'plasma-nm']:
    for src in (work/'cellular/sources'/source/'LICENSES').glob('*.txt'):
        copy(src,'usr/share/licenses/houji-cellular/'+source+'/'+src.name)
write('usr/share/applications/kcm_houji_sim.desktop',
      '[Desktop Entry]\nType=Application\nName=SIM Cards and eSIM\nIcon=network-mobile\n'
      'Exec=plasma-open-settings kcm_houji_sim\nNoDisplay=true\n'
      'Categories=Qt;KDE;Settings;Network;\n')
run('depmod','-b',root,release)
print('Staged clean Houji userspace and modules:',release)
