# -*- coding: utf-8 -*-
__title__ = "Madeiramento: Tesouras"
__author__ = "AutoBIM"
__version__ = "Versão 2.0"
__doc__ = """Gera automaticamente famílias nativas de Tesouras (Estribada Rainha, Simples, Howe e Fink) para telhados de 1 ou 2 águas, com entalhes de encaixe e desconto automático da trama."""

import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

rf.run_tesouras()
