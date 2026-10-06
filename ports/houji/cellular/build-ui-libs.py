#!/usr/bin/python3
"""Build the patched KDE cellular model against pinned target headers/libraries.

Only ModemManagerQt and Plasma NM's cellular library need replacement. Their
public SONAMEs and the existing QML plugin entry point are retained. Qt's target
moc runs under qemu on x86 hosts, matching the Settings-module build.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import platform
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--sysroot', type=Path, required=True)
    args = parser.parse_args()
    work, root = args.work.resolve(), args.sysroot.resolve()
    output = work/'ui-libs'
    output.mkdir(exist_ok=True)
    target = work/'stage/lib'
    target.mkdir(parents=True, exist_ok=True)
    qt, kf, libs = root/'usr/include/qt6', root/'usr/include/KF6', root/'usr/lib64'
    loader = libs/'ld-linux-aarch64.so.1'
    loader.chmod(0o755)
    runner = []
    if platform.machine() != 'aarch64':
        emulator = shutil.which('qemu-aarch64-static')
        if not emulator:
            raise RuntimeError('KDE generators need qemu-aarch64-static on non-arm64 hosts')
        runner.append(emulator)
    moc = [*runner, loader, '--library-path', libs, libs/'qt6/libexec/moc']

    def build(name, source, sources, headers, extra, defines, links, soname):
        dest = output/name
        dest.mkdir(exist_ok=True)
        includes = [dest, source, root/'usr/include', root/'usr/include/ModemManager',
                    root/'usr/include/libnm', root/'usr/include/glib-2.0', libs/'glib-2.0/include', qt]
        includes += [qt/x for x in ('QtCore','QtDBus','QtXml','QtQml','QtQmlIntegration','QtNetwork','QtGui')]
        includes += [kf/x for x in ('ModemManagerQt','NetworkManagerQt','KCoreAddons','KI18n')]
        includes += [root/'usr/include'/x for x in ('qcoro6','qcoro6/QCoro','qcoro6/qcoro')]
        includes += extra
        flags = ['-I'+str(p) for p in includes]
        definitions = ['-DQT_NO_KEYWORDS', *defines]
        for header in headers:
            if 'Q_OBJECT' in header.read_text():
                run(*moc, *flags, *definitions, header, '-o', dest/('moc_'+header.stem+'.cpp'))
        cpp = list(sources)
        included = '\n'.join(p.read_text() for p in cpp)
        for header in headers:
            generated = dest/('moc_'+header.stem+'.cpp')
            if not generated.exists() or generated.name in included:
                continue
            if name == 'plasma':
                wrapper = dest/('include_'+generated.name)
                wrapper.write_text('#include "cellularmodem.h"\n#include "cellularsim.h"\n'
                                   '#include "'+generated.name+'"\n')
                cpp.append(wrapper)
            else:
                cpp.append(generated)

        def compile_one(path):
            relative = path.relative_to(source) if path.is_relative_to(source) else Path(path.name)
            obj = dest/(str(relative).replace('/', '_')+'.o')
            run('aarch64-linux-gnu-g++', '-std=c++20', '-O2', '-fPIC', '-mno-outline-atomics',
                '-ffile-prefix-map='+str(work)+'=houji-cellular-build',
                *flags, *definitions, '-c', path, '-o', obj)
            return obj

        with ThreadPoolExecutor(max_workers=8) as pool:
            objects = list(pool.map(compile_one, cpp))
        run('aarch64-linux-gnu-g++', '-shared', '-Wl,--no-undefined', '-Wl,-soname,'+soname,
            *objects, '-L'+str(libs), '-Wl,-rpath-link,'+str(libs),
            *['-l'+name for name in links], '-o', target/soname)

    mm = work/'sources/modemmanager-qt/src'
    build('mmqt', mm, [*sorted(mm.glob('*.cpp')), *sorted(mm.glob('dbus/*.cpp'))],
          [*sorted(mm.glob('*.h')), *sorted(mm.glob('dbus/*.h'))], [], ['-DKF6ModemManagerQt_EXPORTS'],
          ['Qt6Core','Qt6DBus','Qt6Xml'], 'libKF6ModemManagerQt.so.6')
    pl = work/'sources/plasma-nm/libs/cellular'
    plo = output/'plasma'
    plo.mkdir(exist_ok=True)
    for name in ('plasmanm_cellular','plasmanm_editor'):
        (plo/(name+'_export.h')).write_text('#pragma once\n#define '+name.upper()
                                          +'_EXPORT __attribute__((visibility("default")))\n')
    (plo/'plasma_nm_cellular.h').write_text(
        '#pragma once\n#include <QLoggingCategory>\nQ_DECLARE_LOGGING_CATEGORY(PLASMA_NM_CELLULAR_LOG)\n')
    # Equivalent to ecm_add_qml_module's generated registration for these five
    # QML_ELEMENT classes; keep the symbol expected by the installed plugin.
    registration = plo/'register.cpp'
    registration.write_text('''#include <QLoggingCategory>
#include <QtQml/qqml.h>
#include <QtQml/qqmlmoduleregistration.h>
#include "cellularmodem.h"
#include "cellularmodemlist.h"
#include "cellularsim.h"
#include "cellularmodemdetails.h"
#include "cellularconnectionprofile.h"
Q_LOGGING_CATEGORY(PLASMA_NM_CELLULAR_LOG,"org.kde.plasma.nm.cellular")
void qml_register_types_org_kde_plasma_networkmanagement_cellular() {
    const char *uri = "org.kde.plasma.networkmanagement.cellular";
    qmlRegisterTypesAndRevisions<CellularModem,CellularModemList,CellularSim,CellularModemDetails,CellularConnectionProfile>(uri,1);
    qmlRegisterModule(uri,1,0);
}
static const QQmlModuleRegistration registration("org.kde.plasma.networkmanagement.cellular",qml_register_types_org_kde_plasma_networkmanagement_cellular);
''')
    build('plasma', pl, [*sorted(pl.glob('*.cpp')), registration], sorted(pl.glob('*.h')),
          [work/'sources/plasma-nm/libs/editor'], [],
          ['Qt6Core','Qt6Gui','Qt6DBus','Qt6Xml','Qt6Qml','KF6I18n','KF6CoreAddons',
           'KF6ModemManagerQt','KF6NetworkManagerQt','QCoro6Core','QCoro6DBus','plasmanm_editor'],
          'libplasmanm_cellular.so')


if __name__ == '__main__':
    main()
