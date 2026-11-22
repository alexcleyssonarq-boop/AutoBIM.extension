# -*- coding: utf-8 -*-
__title__ = "Pranchas\nAuto"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Duplica pranchas completas (mantendo carimbo, tabelas, legendas, textos e duplicando vistas) ou faz Auto-Montagem distribuindo múltiplas vistas em novas folhas."""

import traceback
from pyrevit import forms
import Snippets._doc_extra as de
try:
    reload(de)
except Exception:
    pass

try:
    de.run_pranchas_auto()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Pranchas Auto")
