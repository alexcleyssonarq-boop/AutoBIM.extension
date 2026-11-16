# -*- coding: utf-8 -*-
__title__ = "Madeiramento: Pontaletes"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera automaticamente Pontaletes de apoio em Madeira (com Berço e Terças) ou Metálicos (Aço Galvanizado em Perfil U, U Enrijecido/C ou Tubo Metalon com Sapata), posicionados automaticamente sob o telhado com base nivelada (igual às Tesouras)."""

import traceback
from pyrevit import forms
import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

try:
    rf.run_pontaletes()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Pontaletes")
