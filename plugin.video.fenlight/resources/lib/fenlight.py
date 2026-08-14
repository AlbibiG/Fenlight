# -*- coding: utf-8 -*-
import sys
from modules.router import routing, sys_exit_check
from modules.kodi_utils import enable_debug_tracing

enable_debug_tracing()
routing(sys)
if sys_exit_check(): sys.exit(1)
