import sys
from urllib.parse import urlencode
import xbmc

params = dict(arg.split('=', 1) for arg in sys.argv[1:] if '=' in arg)
mode = params.get('mode', '')
if mode.startswith('queue.') and mode != 'queue.open':
    for key in ('season', 'episode'):
        if key in params:
            try: params[key] = str(int(float(params[key])))
            except ValueError: pass
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.fenlight/?%s)' % urlencode(params))
