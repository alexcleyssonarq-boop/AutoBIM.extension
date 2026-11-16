# -*- coding: utf-8 -*-
__title__ = "Vistas por\nAmbiente"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera automaticamente Plantas recortadas (CropBox), Vistas 3D Isométricas (Section Box) e as 4 Elevações Internas (Norte, Sul, Leste, Oeste) para cada Ambiente (Room) selecionado."""

import traceback
from pyrevit import forms
import Snippets._doc_extra as de
try:
    reload(de)
except Exception:
    pass

try:
    de.run_vistas_ambiente()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Vistas por Ambiente")
