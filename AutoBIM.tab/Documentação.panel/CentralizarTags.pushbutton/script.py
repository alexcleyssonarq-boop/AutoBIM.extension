# -*- coding: utf-8 -*-
__title__ = "Centralizar\nRoom Tags"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Reposiciona automaticamente os pontos de referência das Rooms e suas Room Tags para o centro interno seguro de cada ambiente (inclusive formatos em L ou U)."""

import traceback
from pyrevit import forms
import Snippets._doc_extra as de
try:
    reload(de)
except Exception:
    pass

try:
    de.run_centralizar_tags()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Centralizar Room Tags")
