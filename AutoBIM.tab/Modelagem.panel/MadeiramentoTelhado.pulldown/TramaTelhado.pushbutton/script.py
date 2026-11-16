# -*- coding: utf-8 -*-
__title__ = "Madeiramento: Ripas, Caibros e Terças"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera automaticamente a trama de madeira (Ripas, Caibros e Terças) sob as águas dos telhados selecionados.

Empilhamento e Recorte 3D:
Calcula a inclinação exata de cada água do telhado, recorta as peças nas diagonais de espigões e rincões e empilha automaticamente as camadas (Ripas, Caibros e Terças) com as bitolas e espaçamentos configurados."""

import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

rf.run_trama()
