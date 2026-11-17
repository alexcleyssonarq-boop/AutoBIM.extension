# -*- coding: utf-8 -*-
__title__ = "Cotar Seleção\nno Corte"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Semelhante ao 'Cotar Paredes Selecionadas' da planta, voltado para alturas em Corte: selecione (ou pré-selecione com caixa) pisos/lajes, forros, telhados, vigas, pilares ou paredes no Corte ativo.

O comando cria a cadeia vertical externa (camadas/alturas) e a cota de altura total automaticamente pelo lado de fora do desenho sem colidir com cotas já existentes."""

import Snippets._section_dims as sd

if __name__ == "__main__":
    sd.run_cotar_selecao_corte(abrir_config=False)
