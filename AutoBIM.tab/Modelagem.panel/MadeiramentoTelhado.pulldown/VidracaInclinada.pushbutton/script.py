# -*- coding: utf-8 -*-
__title__ = "Madeiramento: Vidraça Inclinada"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera o madeiramento do telhado (Ripas, Caibros e Terças) criando e empilhando automaticamente telhados do tipo Vidraça Inclinada (Sloped Glazing).

Edição de Perímetro Nativa:
Duplica o telhado selecionado para cada camada ativa, cria automaticamente os tipos de Montantes Retangulares e de Vidraça Inclinada nas bitolas e espaçamentos escolhidos e agrupa os elementos ao concluir."""

import Snippets._roof_framing as rf
try:
    reload(rf)
except Exception:
    pass

rf.run_vidraca_inclinada()
