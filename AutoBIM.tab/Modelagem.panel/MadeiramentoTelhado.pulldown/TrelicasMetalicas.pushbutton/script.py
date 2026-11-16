# -*- coding: utf-8 -*-
__title__ = "Estrutura Metálica: Treliças"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera automaticamente Treliças Metálicas em 1 ou 2 águas (Triangular, Paralela no Caimento ou Reta Horizontal) em Aço Galvanizado com perfis comerciais separados para Banzos e Montantes/Diagonais (Metalon, Perfil U Enrijecido/C, U Simples ou I)."""

import traceback
from pyrevit import forms
import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

try:
    rf.run_trelicas_metalicas()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Treliças Metálicas")
