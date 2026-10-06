#!/usr/bin/python3
"""Cross-build Houji's QTEE license relay and QRTR-only eSIM client."""
import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import checkout, cross_file, fetch, run

ASSETS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--sysroot', type=Path, required=True)
    args = parser.parse_args()
    work, sysroot = args.work.resolve(), args.sysroot.resolve()
    work.mkdir(parents=True, exist_ok=True)
    stage = work/'stage'
    prefix_map = '-ffile-prefix-map='+str(work)+'=houji-cellular-build'
    pins = json.loads((ASSETS/'sources.json').read_text())
    for name, pin in pins['git'].items():
        patch = {'minkipc': 'rpmb-read-only.patch', 'lpac': 'lpac-activation-stdin.patch',
                 'ModemManager': 'modemmanager-primary-gw.patch',
                 'modemmanager-qt': 'modemmanager-qt-lifecycle.patch',
                 'plasma-nm': 'plasma-nm-data-toggle.patch'}.get(name)
        checkout(pin['url'], pin['revision'], work/'sources'/name,
                 ASSETS/patch if patch else None)
        if name == 'ModemManager':
            checkout(pin['url'], pin['revision'], work/'sources'/name, ASSETS/'modemmanager-qrtr-resume.patch')
    compiler = fetch(pins['idlc']['url'], work/'idlc-bin/idlc', pins['idlc']['sha256'])
    compiler.chmod(0o755)
    generator = work/'idlc/idlc'
    generator.parent.mkdir(exist_ok=True)
    command = [str(compiler)]
    if platform.machine() == 'aarch64':
        emulator = shutil.which('qemu-x86_64-static')
        if not emulator:
            raise RuntimeError('The pinned IDL compiler needs qemu-x86_64-static on arm64 build hosts')
        command.insert(0, emulator)
    generator.write_text('#!/bin/sh\nexec '+shlex.join(command)+' "$@"\n')
    generator.chmod(0o755)
    cross_file(work, sysroot)
    toolchain = work/'toolchain.cmake'
    toolchain.write_text(f'''set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_FIND_ROOT_PATH "{sysroot}" "{stage}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(PKG_CONFIG_EXECUTABLE "{work}/pkg-config")
set(CMAKE_EXE_LINKER_FLAGS_INIT "-L{sysroot}/usr/lib64 -Wl,-rpath-link,{sysroot}/usr/lib64")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-L{sysroot}/usr/lib64 -Wl,-rpath-link,{sysroot}/usr/lib64")
''')

    def cmake(name, *flags):
        run('cmake', '-S', work/'sources'/name, '-B', work/('build-'+name),
            '-DCMAKE_TOOLCHAIN_FILE='+str(toolchain), '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_POSITION_INDEPENDENT_CODE=ON', '-DCMAKE_INSTALL_PREFIX='+str(stage),
            '-DCMAKE_INSTALL_LIBDIR=lib', '-DCMAKE_INSTALL_RPATH=',
            '-DCMAKE_C_FLAGS='+prefix_map, *flags)
        run('cmake', '--build', work/('build-'+name), '-j8')
        run('cmake', '--install', work/('build-'+name))

    cmake('QCBOR', '-DBUILD_QCBOR_TEST=OFF')
    cmake('quic-teec', '-DQCBOR_DIR_HINT='+str(stage))
    cmake('minkipc', '-DQCBOR_DIR_HINT='+str(stage), '-DQCOMTEE_DIR_HINT='+str(stage),
          '-DMINKIDLC_BIN_DIR='+str(generator.parent), '-DBUILD_MINKTEEC=OFF',
          '-DBUILD_UNITTEST=OFF', '-DBUILD_PKCS11=OFF', '-DBUILD_RPMB_CLIENT=OFF',
          '-DBUILD_RPMB_LISTENER=ON', '-DBUILD_TA_AUTOLOAD_LISTENER=ON',
          '-DBUILD_TIME_LISTENER=ON', '-DBUILD_FS_LISTENER=ON', '-DBUILD_GPFS_LISTENER=ON',
          '-DCMAKE_CXX_FLAGS=-mno-outline-atomics '+prefix_map)
    run('aarch64-linux-gnu-gcc', '-Wall', '-Wextra', '-Werror', '-O2', '-shared', '-fPIC',
        '-I'+str(work/'sources/quic-teec/libqcomtee/include'), prefix_map,
        '-ffile-prefix-map='+str(ASSETS)+'=houji-cellular', ASSETS/'qtee.c',
        stage/'lib/libqcomtee.a', stage/'lib/libqcbor.a', '-pthread',
        '-o', stage/'lib/libhouji-qtee.so')
    # lpac's pkg-config probes must use the same pinned target headers/libraries.
    env = dict(os.environ, PKG_CONFIG=str(work/'pkg-config'))
    run('cmake', '-S', work/'sources/lpac', '-B', work/'build-lpac',
        '-DCMAKE_TOOLCHAIN_FILE='+str(toolchain), '-DCMAKE_BUILD_TYPE=Release',
        '-DLPAC_WITH_APDU_QMI_QRTR=ON', '-DLPAC_WITH_APDU_PCSC=OFF', '-DLPAC_WITH_APDU_AT=OFF',
        '-DLPAC_WITH_APDU_QMI=OFF', '-DLPAC_WITH_APDU_MBIM=OFF', '-DLPAC_WITH_HTTP_CURL=ON',
        '-DCMAKE_C_FLAGS=-I'+str(sysroot/'usr/include')+' '+prefix_map,
        '-DCURL_INCLUDE_DIR='+str(sysroot/'usr/include'),
        '-DCURL_LIBRARY='+str(sysroot/'usr/lib64/libcurl.so.4'), env=env)
    run('cmake', '--build', work/'build-lpac', '-j8')
    shutil.copy2(work/'build-lpac/output/lpac', stage/'bin/lpac')
    run('python3', ASSETS/'build-settings.py', '--work', work, '--sysroot', sysroot)
    run('python3', ASSETS/'build-ui-libs.py', '--work', work, '--sysroot', sysroot)
    # The pinned base predates its WWAN subpackage. Build the two plugins from
    # the same NM release instead of installing an ABI-mismatched Fedora RPM.
    nm = work/'build-NetworkManager'
    options = dict(dist_version='1.58.1-1.fc44.armada', docs='false', man='false',
                   tests='no', introspection='false', vapi='false', wifi='false', ppp='false',
                   nmtui='false', nmcli='false', nm_cloud_setup='false', ovs='false', nbft='false',
                   clat='false', polkit='false', selinux='false', libaudit='no', crypto='null',
                   libpsl='false', concheck='false', modem_manager='true', qt='false', readline='none',
                   mobile_broadband_provider_info_database='/usr/share/mobile-broadband-provider-info/serviceproviders.xml',
                   systemdsystemunitdir='/usr/lib/systemd/system',
                   systemdsystemgeneratordir='/usr/lib/systemd/system-generators', udev_dir='/usr/lib/udev')
    run('meson', 'setup', *(['--reconfigure'] if (nm/'build.ninja').exists() else []),
        nm, work/'sources/NetworkManager', '--cross-file', work/'cross.ini', '--prefix=/usr',
        '--libdir=lib64', '-Dc_args=-I'+str(sysroot/'usr/include')+' '+prefix_map,
        *['-D'+k+'='+v for k,v in options.items()])
    plugins = ['libnm-wwan.so', 'libnm-device-plugin-wwan.so']
    run('ninja', '-C', nm, *['src/core/devices/wwan/'+name for name in plugins])
    for name in plugins:
        shutil.copy2(nm/'src/core/devices/wwan'/name, stage/'lib'/name)
        run('patchelf', '--set-rpath', '$ORIGIN', stage/'lib'/name)
    mm = work/'build-ModemManager'
    run('meson', 'setup', *(['--reconfigure'] if (mm/'build.ninja').exists() else []),
        mm, work/'sources/ModemManager', '--cross-file', work/'cross.ini', '--prefix=/usr',
        '--libdir=lib64', '-Dc_args=-I'+str(sysroot/'usr/include')+' '+prefix_map,
        '-Dtests=false', '-Dexamples=false', '-Dman=false', '-Dgtk_doc=false',
        '-Dintrospection=false', '-Dvapi=false', '-Dmbim=false', '-Dqmi=true', '-Dqrtr=true',
        '-Dpolkit=strict', '-Dbuiltin_plugins=true', '-Dauto_features=disabled',
        '-Dplugin_qcom_soc=enabled', '-Dplugin_generic=enabled', '-Dudevdir=/usr/lib/udev',
        '-Dsystemdsystemunitdir=/usr/lib/systemd/system', '-Ddbus_policy_dir=/usr/share/dbus-1/system.d',
        '-Dbash_completion=false')
    run('ninja', '-C', mm, 'src/ModemManager')
    shutil.copy2(mm/'src/ModemManager', stage/'bin/ModemManager')
    run('patchelf', '--remove-rpath', stage/'bin/ModemManager')
    for binary in list((stage/'bin').glob('*')) + list((stage/'lib').glob('*.so*')):
        if binary.is_file() and not binary.is_symlink() and binary.read_bytes()[:4] == b'\x7fELF':
            run('aarch64-linux-gnu-strip', '--strip-unneeded', binary)
    print('Built cellular tools:', stage)


if __name__ == '__main__':
    main()
