# -*- coding: utf-8 -*-
__title__ = "Estrutura Metálica: Vidraça Inclinada"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera a Estrutura Metálica de Cobertura (Terças em Perfil C/U/Metalon + Vigas Principais em Perfil I/W/C) empilhando telhados nativos do tipo Vidraça Inclinada (Sloped Glazing) com perfis metálicos nos montantes e edição prática de perímetro."""

import traceback
from pyrevit import forms
import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

try:
    rf.run_estrutura_metalica_vidraca()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Estrutura Metálica (Vidraça Inclinada)")
