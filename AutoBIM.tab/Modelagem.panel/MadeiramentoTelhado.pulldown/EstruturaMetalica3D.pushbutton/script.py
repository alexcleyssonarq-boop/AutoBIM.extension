# -*- coding: utf-8 -*-
__title__ = "Estrutura Metálica: Vigas e Terças (3D)"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera automaticamente a Estrutura Metálica de Cobertura em peças 3D (Quadros Estruturais em Aço Galvanizado), combinando Terças horizontais (Perfil U Enrijecido/C, U Simples, Metalon ou I) apoiadas sobre Vigas Principais inclinadas no sentido do caimento (Perfil I/W, C, Metalon ou U)."""

import traceback
from pyrevit import forms
import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

try:
    rf.run_estrutura_metalica_3d()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Estrutura Metálica (3D)")
