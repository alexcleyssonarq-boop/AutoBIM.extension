# -*- coding: utf-8 -*-
"""Hook doc-opened do AutoBIM: avisa na thread de UI (1x por sessao) se houver nova versao no GitHub."""

import os
import sys

try:
    _root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    _lib_dir = os.path.join(_root_dir, "lib")
    if _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
    import Snippets._updater as _upd
    _upd.notificar_se_houver_atualizacao()
except Exception:
    pass
