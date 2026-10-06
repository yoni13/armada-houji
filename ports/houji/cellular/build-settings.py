#!/usr/bin/python3
"""Cross-build the small KCM with target headers and matching host Qt generators."""
import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import run

ASSETS = Path(__file__).resolve().parent/'settings'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--sysroot', type=Path, required=True)
    a = p.parse_args()
    out, root = a.work.resolve()/'settings-build', a.sysroot.resolve()
    out.mkdir(parents=True, exist_ok=True)
    qt = root/'usr/include/qt6'
    kf = root/'usr/include/KF6'
    # Use the generators from the pinned target RPM, including on Ubuntu CI
    # whose host Qt is older. The target loader makes the same C library usable
    # on native arm64 hosts and under qemu-user on x86_64.
    libs = root/'usr/lib64'
    loader = libs/'ld-linux-aarch64.so.1'
    loader.chmod(0o755)
    runner = []
    if platform.machine() != 'aarch64':
        emulator = shutil.which('qemu-aarch64-static')
        if not emulator:
            raise RuntimeError('Qt generators need qemu-aarch64-static on non-arm64 hosts')
        runner.append(emulator)
    runner += [str(loader), '--library-path', str(libs)]
    moc = runner + [str(libs/'qt6/libexec/moc')]
    rcc = runner + [str(libs/'qt6/libexec/rcc')]
    version = subprocess.check_output([*moc, '-v'], text=True, stderr=subprocess.STDOUT)
    if '6.11.' not in version:
        raise RuntimeError('Pinned Qt tools must match the target headers')
    includes = [qt, *[qt/x for x in ('QtCore','QtGui','QtQuick','QtQml','QtQmlIntegration','QtNetwork','QtOpenGL','QtDBus')],
                *[kf/x for x in ('KCoreAddons','KI18n','KCMUtilsQuick','KCMUtilsCore','KCMUtils','KConfig','KConfigCore')]]
    flags = ['-I'+str(x) for x in includes] + ['-I'+str(out), '-I'+str(ASSETS)]
    defines = ['-DKCOREADDONS_LIB', '-DKPLUGINFACTORY_PLUGIN_CLASS_INTERNAL_NAME=kcm_houji_sim_factory',
               '-DQT_NO_KEYWORDS', '-DQT_NO_DEBUG', '-DQT_CORE_LIB', '-DQT_DBUS_LIB',
               '-DQT_GUI_LIB','-DQT_QUICK_LIB','-DQT_QML_LIB']
    run(*moc, *flags, *defines, ASSETS/'settings.cpp', '-o', out/'settings.moc')
    tree = ET.Element('RCC')
    resource = ET.SubElement(tree, 'qresource', prefix='/kcm/kcm_houji_sim')
    ET.SubElement(resource, 'file', alias='main.qml').text = str(ASSETS/'ui/main.qml')
    ET.ElementTree(tree).write(out/'settings.qrc', encoding='unicode')
    run(*rcc, out/'settings.qrc', '-o', out/'resources.cpp')
    target = a.work.resolve()/'stage/lib/kcm_houji_sim.so'
    target.parent.mkdir(parents=True, exist_ok=True)
    run('aarch64-linux-gnu-g++', '-std=c++17', '-O2', '-mno-outline-atomics', '-fPIC', '-shared', *flags, *defines,
        '-ffile-prefix-map='+str(a.work.resolve())+'=houji-cellular-build',
        '-ffile-prefix-map='+str(ASSETS)+'=houji-cellular-settings',
        ASSETS/'settings.cpp', out/'resources.cpp', '-L'+str(root/'usr/lib64'),
        '-Wl,-rpath-link,'+str(root/'usr/lib64'), '-Wl,--no-undefined',
        *['-l'+x for x in ('KF6KCMUtilsQuick','KF6KCMUtilsCore','KF6CoreAddons','KF6ConfigCore',
                          'KF6I18n','Qt6Quick','Qt6Qml','Qt6Gui','Qt6DBus','Qt6Core')], '-o', target)
    print('Built Plasma Mobile Settings module:', target)


if __name__ == '__main__':
    main()
