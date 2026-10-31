#!/usr/bin/python3
"""Steam side of a temporary test shortcut, through Steam's own UI calls (run as the Game Mode user).

create NAME EXE      add a Lepton (Armada) shortcut for the APK at the absolute path EXE, print its app id
run APPID | stop APPID | remove APPID
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import time

loader = importlib.machinery.SourceFileLoader('resume', '/usr/libexec/armada/houji-steam-resume')
spec = importlib.util.spec_from_loader('resume', loader)
resume = importlib.util.module_from_spec(spec)
loader.exec_module(resume)
cef = resume.Cef()
command = sys.argv[1]
if command == 'create':
    launch = {'name': sys.argv[2], 'exe': sys.argv[3], 'startDir': os.path.dirname(sys.argv[3]),
              'launchOptions': '/usr/libexec/armada/armada-game-launch %command%', 'compatTool': 'lepton_armada'}
    script = """window.__armada_add = null; (async () => { try {
        const c = SteamClient.Apps; const s = %s;
        const appid = Number(await c.AddShortcut(s.name, s.exe, s.startDir, s.launchOptions));
        if (!appid) throw new Error('no appid');
        try { c.SetShortcutName(appid, s.name); } catch (e) {}
        try { c.SetShortcutLaunchOptions(appid, s.launchOptions); } catch (e) {}
        await c.SpecifyCompatTool(appid, s.compatTool);
        window.__armada_add = String(appid);
    } catch (e) { window.__armada_add = 'error: ' + e; } })(); 'started'""" % json.dumps(launch)
    cef.evaluate(script)
    for _ in range(60):
        time.sleep(0.5)
        value = cef.evaluate('String(window.__armada_add)')
        if value != 'null':
            print(value)
            break
    else:
        print('error: timed out')
else:
    appid = int(sys.argv[2])
    game_id = str((appid << 32) | 0x02000000)
    expression = {'run': 'SteamClient.Apps.RunGame("%s", "", -1, 100)' % game_id,
                  'stop': 'SteamClient.Apps.TerminateApp("%s", false)' % game_id,
                  'remove': 'SteamClient.Apps.RemoveShortcut(%d)' % appid}[command]
    print(cef.evaluate(expression + '; "ok"'))
