# -*- coding: utf-8 -*-
"""Startup script do AutoBIM: checa atualizacoes no GitHub em segundo plano sem travar o Revit."""

import os
import sys

try:
    _lib_dir = os.path.join(os.path.dirname(__file__), "lib")
    if _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
    import Snippets._updater as _upd
    _upd.iniciar_checagem_background()
except Exception:
    pass
