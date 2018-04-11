# -*- coding: utf-8 -*-
#
# Copyright 2016 Taŭga Tecnologia
#   Aristides Caldeira <aristides.caldeira@tauga.com.br>
# License AGPL-3 or later (http://www.gnu.org/licenses/agpl)
#

from __future__ import division, print_function, unicode_literals

import os
import logging
import base64
from io import BytesIO
from builtins import str

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_br_base.constante_tributaria import *

_logger = logging.getLogger(__name__)

from ..versao_nfe_padrao import ClasseVol

try:
    from pysped.nfe.webservices_flags import *
    from pysped.nfe.leiaute import *
    from pybrasil.inscricao import limpa_formatacao
    from pybrasil.data import (parse_datetime, UTC, data_hora_horario_brasilia,
                               agora)
    from pybrasil.valor import formata_valor
    from pybrasil.valor.decimal import Decimal as D
    from pysped.nfe.leiaute import Pag_400, DetPag_400

except (ImportError, IOError) as err:
    _logger.debug(err)

from ..versao_nfe_padrao import ClasseNFe, ClasseNFCe, ClasseProcNFe, \
    ClasseReboque


class SpedDocumento(models.Model):
    _inherit = 'sped.documento'

    def monta_nfe(self, processador=None):
        self.ensure_one()

        if self.modelo == MODELO_FISCAL_NFE:
            nfe = ClasseNFe()
        elif self.modelo == MODELO_FISCAL_NFCE:
            nfe = ClasseNFCe()
        else:
            return

        #
        # Identificação da NF-e
        #
        self._monta_nfe_identificacao(nfe.infNFe.ide)

        #
        # Notas referenciadas
        #
        for doc_ref in self.documento_referenciado_ids:
            nfe.infNFe.ide.NFref.append(doc_ref.monta_nfe())

        #
        # Emitente
        #
        self._monta_nfe_emitente(nfe.infNFe.emit)

        #
        # Destinatário
        #
        self._monta_nfe_destinatario(nfe.infNFe.dest)

        #
        # Endereço de entrega e retirada
        #
        self._monta_nfe_endereco_retirada(nfe.infNFe.retirada)
        self._monta_nfe_endereco_entrega(nfe.infNFe.entrega)

        #
        # Itens
        #
        i = 1
        for item in self.item_ids:
            nfe.infNFe.det.append(item.monta_nfe(i, nfe))
            i += 1

        #
        # Transporte e frete
        #
        self._monta_nfe_transporte(nfe.infNFe.transp)

        #
        # Duplicatas e pagamentos
        #
        self._monta_nfe_pagamentos(nfe)

        #
        # Totais
        #
        self._monta_nfe_total(nfe)

        #
        # Informações adicionais
        #
        nfe.infNFe.infAdic.infCpl.valor = \
            self._monta_nfe_informacao_complementar(nfe)
        nfe.infNFe.infAdic.infAdFisco.valor = \
            self._monta_nfe_informacao_fisco()

        nfe.gera_nova_chave()
        nfe.monta_chave()

        if self.empresa_id.certificado_id:
            certificado = self.empresa_id.certificado_id.certificado_nfe()
            certificado.assina_xmlnfe(nfe)

        return nfe

    def _monta_nfe_identificacao(self, ide):
        empresa = self.empresa_id
        partner = self.partner_id

        ide.tpAmb.valor = int(self.ambiente_nfe)
        ide.tpNF.valor = int(self.entrada_saida)
        ide.cUF.valor = UF_CODIGO[empresa.estado]
        ide.natOp.valor = self.natureza_operacao_id.nome
        ide.indPag.valor = int(self.ind_forma_pagamento)
        ide.serie.valor = self.serie
        ide.nNF.valor = str(D(self.numero).quantize(D('1')))

        #
        # Tratamento das datas de UTC para o horário de brasília
        #
        ide.dhEmi.valor = data_hora_horario_brasilia(
            parse_datetime(self.data_hora_emissao + ' GMT')
        )
        ide.dEmi.valor = ide.dhEmi.valor

        if self.data_hora_entrada_saida:
            ide.dhSaiEnt.valor = data_hora_horario_brasilia(
                parse_datetime(self.data_hora_entrada_saida + ' GMT')
            )
        else:
            ide.dhSaiEnt.valor = data_hora_horario_brasilia(
                parse_datetime(self.data_hora_emissao + ' GMT')
            )

        ide.dSaiEnt.valor = ide.dhSaiEnt.valor
        ide.hSaiEnt.valor = ide.dhSaiEnt.valor

        ide.cMunFG.valor = ('%s%s') % (
            empresa.state_id.ibge_code, empresa.l10n_br_city_id.ibge_code)
        # ide.tpImp.valor = 1  # DANFE em retrato
        ide.tpEmis.valor = self.tipo_emissao_nfe
        ide.finNFe.valor = self.finalidade_nfe
        ide.procEmi.valor = 0  # Emissão por aplicativo próprio
        ide.verProc.valor = 'Odoo ERP'
        ide.indPres.valor = self.presenca_comprador

        #
        # NFC-e é sempre emissão para dentro do estado
        # e sempre para consumidor final
        #
        if self.modelo == MODELO_FISCAL_NFCE:
            ide.idDest.valor = IDENTIFICACAO_DESTINO_INTERNO
            ide.indFinal.valor = TIPO_CONSUMIDOR_FINAL_CONSUMIDOR_FINAL

        else:
            if partner.estado == 'EX':
                ide.idDest.valor = IDENTIFICACAO_DESTINO_EXTERIOR
            elif partner.estado == empresa.estado:
                ide.idDest.valor = IDENTIFICACAO_DESTINO_INTERNO
            else:
                ide.idDest.valor = IDENTIFICACAO_DESTINO_INTERESTADUAL

            if (self.consumidor_final) or (
                        partner.contribuinte ==
                        INDICADOR_IE_DESTINATARIO_NAO_CONTRIBUINTE):
                ide.indFinal.valor = TIPO_CONSUMIDOR_FINAL_CONSUMIDOR_FINAL

    def _monta_nfe_emitente(self, emit):
        empresa = self.empresa_id

        emit.CNPJ.valor = limpa_formatacao(empresa.cnpj_cpf)
        emit.xNome.valor = empresa.legal_name
        emit.xFant.valor = empresa.fantasia or ''
        emit.enderEmit.xLgr.valor = empresa.street
        emit.enderEmit.nro.valor = empresa.number
        emit.enderEmit.xCpl.valor = empresa.street2 or ''
        emit.enderEmit.xBairro.valor = empresa.district
        emit.enderEmit.cMun.valor = ('%s%s') % (
            empresa.state_id.ibge_code, empresa.l10n_br_city_id.ibge_code)
        emit.enderEmit.xMun.valor = empresa.city
        emit.enderEmit.UF.valor = empresa.estado
        emit.enderEmit.CEP.valor = limpa_formatacao(empresa.zip)
        # emit.enderEmit.cPais.valor = '1058'
        # emit.enderEmit.xPais.valor = 'Brasil'
        emit.enderEmit.fone.valor = limpa_formatacao(empresa.phone or '')
        emit.IE.valor = limpa_formatacao(empresa.inscr_est or '')
        #
        # Usa o regime tributário da NF e não da empresa, e trata o código
        # interno 3.1 para o lucro real, que na NF deve ir somente 3
        #
        emit.CRT.valor = self.regime_tributario[0]

        if self.modelo == MODELO_FISCAL_NFCE:
            emit.csc.id = empresa.csc_id or 1
            emit.csc.codigo = empresa.csc_codigo or ''
            # emit.csc.codigo = emit.csc.codigo.strip()

    def _monta_nfe_destinatario(self, dest):
        partner = self.partner_id

        #
        # Para a NFC-e, o destinatário é sempre não contribuinte
        #
        if self.modelo == MODELO_FISCAL_NFCE:
            dest.indIEDest.valor = INDICADOR_IE_DESTINATARIO_NAO_CONTRIBUINTE

        else:
            dest.indIEDest.valor = partner.contribuinte

            if partner.contribuinte == \
                    INDICADOR_IE_DESTINATARIO_CONTRIBUINTE:
                dest.IE.valor = limpa_formatacao(partner.inscr_est or '')

        #
        # Trata a possibilidade de ausência do destinatário na NFC-e
        #
        if self.modelo == MODELO_FISCAL_NFCE and not partner.cnpj_cpf:
            return

        #
        # partners estrangeiros tem a ID de estrangeiro sempre começando
        # com EX
        #
        if partner.cnpj_cpf.startswith('EX'):
            dest.idEstrangeiro.valor = \
                limpa_formatacao(partner.cnpj_cpf or '')

        elif len(partner.cnpj_cpf or '') == 14:
            dest.CPF.valor = limpa_formatacao(partner.cnpj_cpf)

        elif len(partner.cnpj_cpf or '') == 18:
            dest.CNPJ.valor = limpa_formatacao(partner.cnpj_cpf)

        #if self.ambiente_nfe == AMBIENTE_NFE_HOMOLOGACAO:
            #dest.xNome.valor = 'NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - ' \
                               #'SEM VALOR FISCAL'
        #else:
        dest.xNome.valor = partner.legal_name or ''

        #
        # Para a NFC-e, o endereço do partner pode não ter sido
        # preenchido
        #
        dest.enderDest.xLgr.valor = partner.street or ''
        dest.enderDest.nro.valor = partner.number or ''
        dest.enderDest.xCpl.valor = partner.street2 or ''
        dest.enderDest.xBairro.valor = partner.district or ''

        if not partner.cnpj_cpf.startswith('EX'):
            dest.enderDest.CEP.valor = limpa_formatacao(partner.zip or '')
        else:
            dest.enderDest.CEP.valor = '99999999'

        #
        # Pode haver cadastro de partner sem município para NFC-e
        #
        if partner.l10n_br_city_id:
            dest.enderDest.cMun.valor =('%s%s') % (
                partner.state_id.ibge_code,
                partner.l10n_br_city_id.ibge_code)
            dest.enderDest.xMun.valor = partner.city
            dest.enderDest.UF.valor = partner.estado

            if partner.cnpj_cpf.startswith('EX'):
                dest.enderDest.cPais.valor = \
                    partner.l10n_br_city_id.country_id.codigo_bacen
                dest.enderDest.xPais.valor = \
                    partner.l10n_br_city_id.country_id.name

        dest.enderDest.fone.valor = limpa_formatacao(partner.phone or '')
        email_dest = partner.email or ''
        dest.email.valor = email_dest[:60]

    def _monta_nfe_endereco_retirada(self, retirada):
        return
        if not self.endereco_retirada_id:
            return

        retirada.xLgr.valor = self.endereco_retirada_id.street or ''
        retirada.nro.valor = self.endereco_retirada_id.number or ''
        retirada.xCpl.valor = self.endereco_retirada_id.street2 or ''
        retirada.xBairro.valor = self.endereco_retirada_id.district or ''
        retirada.cMun.valor = ('%s%s') % (
            self.endereco_retirada_id.state_id.ibge_code,
            self.endereco_retirada_id.l10n_br_city_id.ibge_code
        )
        retirada.xMun.valor = self.endereco_retirada_id.city
        retirada.UF.valor = self.endereco_retirada_id.estado

        if self.endereco_retirada_id.cpf:
            if len(self.endereco_retirada_id.cpf) == 18:
                retirada.CNPJ.valor = \
                    limpa_formatacao(self.endereco_retirada_id.cpf)
            else:
                retirada.CPF.valor = \
                    limpa_formatacao(self.endereco_retirada_id.cpf)

    def _monta_nfe_endereco_entrega(self, entrega):
        return
        if not self.endereco_entrega_id:
            return

        entrega.xLgr.valor = self.endereco_entrega_id.street or ''
        entrega.nro.valor = self.endereco_entrega_id.number or ''
        entrega.xCpl.valor = self.endereco_entrega_id.street2 or ''
        entrega.xBairro.valor = self.endereco_entrega_id.district or ''
        entrega.cMun.valor = ('%s%s') % (
            self.endereco_entrega_id.state_id.ibge_code,
            self.endereco_entrega_id.l10n_br_city_id.ibge_code
        )
        entrega.xMun.valor = self.endereco_entrega_id.city
        entrega.UF.valor = self.endereco_entrega_id.estado

        if self.endereco_entrega_id.cnpj_cpf:
            if len(self.endereco_entrega_id.cnpj_cpf) == 18:
                entrega.CNPJ.valor = \
                    limpa_formatacao(self.endereco_entrega_id.cnpj_cpf)
            else:
                entrega.CPF.valor = \
                    limpa_formatacao(self.endereco_entrega_id.cnpj_cpf)

    def _monta_nfe_transporte(self, transp):
        if self.modelo != MODELO_FISCAL_NFE:
            return
        #
        # Temporário até o início da NF-e 4.00
        #
        if self.modalidade_frete == MODALIDADE_FRETE_REMETENTE_PROPRIO:
            transp.modFrete.valor = MODALIDADE_FRETE_REMETENTE_CIF
        elif self.modalidade_frete == MODALIDADE_FRETE_DESTINATARIO_PROPRIO:
            transp.modFrete.valor = MODALIDADE_FRETE_DESTINATARIO_FOB
        else:
            transp.modFrete.valor = \
                self.modalidade_frete or MODALIDADE_FRETE_SEM_FRETE

        if self.transportadora_id:
            if len(self.transportadora_id.cnpj_cpf) == 14:
                transp.transporta.CPF.valor = \
                    limpa_formatacao(self.transportadora_id.cnpj_cpf)
            else:
                transp.transporta.CNPJ.valor = \
                    limpa_formatacao(self.transportadora_id.cnpj_cpf)

            transp.transporta.xNome.valor = \
                self.transportadora_id.legal_name or ''
            transp.transporta.IE.valor = \
                limpa_formatacao(self.transportadora_id.inscr_est or 'ISENTO')
            ender = self.transportadora_id.street or ''
            ender += ' '
            ender += self.transportadora_id.number or ''
            ender += ' '
            ender += self.transportadora_id.street2 or ''
            transp.transporta.xEnder.valor = ender.strip()
            transp.transporta.xMun.valor = self.transportadora_id.city or ''
            transp.transporta.UF.valor = self.transportadora_id.estado or ''

        if self.veiculo_id:
            transp.veicTransp.placa.valor = self.veiculo_id.placa or ''
            transp.veicTransp.UF.valor = self.veiculo_id.estado_id.uf or ''
            transp.veicTransp.RNTC.valor = self.veiculo_id.rntrc or ''

        transp.reboque = []
        if self.reboque_1_id:
            reb = ClasseReboque()
            reb.placa.valor = self.reboque_1_id.placa or ''
            reb.UF.valor = self.reboque_1_id.estado_id.uf or ''
            reb.RNTC.valor = self.reboque_1_id.rntrc or ''
            transp.reboque.append(reb)
        if self.reboque_2_id:
            reb = ClasseReboque()
            reb.placa.valor = self.reboque_2_id.placa or ''
            reb.UF.valor = self.reboque_2_id.estado_id.uf or ''
            reb.RNTC.valor = self.reboque_2_id.rntrc or ''
            transp.reboque.append(reb)
        if self.reboque_3_id:
            reb = ClasseReboque()
            reb.placa.valor = self.reboque_3_id.placa or ''
            reb.UF.valor = self.reboque_3_id.estado_id.uf or ''
            reb.RNTC.valor = self.reboque_3_id.rntrc or ''
            transp.reboque.append(reb)
        if self.reboque_4_id:
            reb = ClasseReboque()
            reb.placa.valor = self.reboque_4_id.placa or ''
            reb.UF.valor = self.reboque_4_id.estado_id.uf or ''
            reb.RNTC.valor = self.reboque_4_id.rntrc or ''
            transp.reboque.append(reb)
        if self.reboque_5_id:
            reb = ClasseReboque()
            reb.placa.valor = self.reboque_5_id.placa or ''
            reb.UF.valor = self.reboque_5_id.estado_id.uf or ''
            reb.RNTC.valor = self.reboque_5_id.rntrc or ''
            transp.reboque.append(reb)

        #
        # Volumes
        #
        transp.vol = []
        if len(self.volume_ids) == 0:
            vol = ClasseVol()

            vol.qVol.valor = str(D(1))
            vol.esp.valor = ''
            vol.marca.valor = ''
            vol.nVol.valor = ''
            vol.pesoL.valor = str(
                D(self.peso_liquido or 0).quantize(D('0.001')))
            vol.pesoB.valor = str(
                D(self.peso_bruto or 0).quantize(D('0.001'))
            )

            transp.vol.append(vol)
        else:
            for volume in self.volume_ids:
                transp.vol.append(volume.monta_nfe())

    def _monta_nfe_cobranca(self, cobr):
        if self.modelo != MODELO_FISCAL_NFE:
            return

        if self.ind_forma_pagamento not in \
                (IND_FORMA_PAGAMENTO_A_VISTA, IND_FORMA_PAGAMENTO_A_PRAZO):
            return
        cobr.fat.nFat.valor = formata_valor(self.numero, casas_decimais=0)
        cobr.fat.vOrig.valor = str(D(self.vr_operacao))
        cobr.fat.vDesc.valor = str(D(self.vr_desconto))
        cobr.fat.vLiq.valor = str(D(self.vr_fatura))

        for duplicata in self.duplicata_ids:
            cobr.dup.append(duplicata.monta_nfe())

    def _monta_nfe_pagamentos(self, nfe):
        if self.modelo not in (MODELO_FISCAL_NFCE, MODELO_FISCAL_NFE):
            return

        for pagamento in self.pagamento_ids:
            if pagamento.condicao_pagamento_id.forma_pagamento == FORMA_PAGAMENTO_DUPLICATA_MERCANTIL:
                self._monta_nfe_cobranca(nfe.infNFe.cobr)
            nfe.infNFe.pag.detPag.append(pagamento.monta_nfe())

        nfe.infNFe.pag.vTroco.valor = str(D(self.vr_troco))

    def _monta_nfe_total(self, nfe):
        nfe.infNFe.total.ICMSTot.vBC.valor = str(D(self.bc_icms_proprio))
        nfe.infNFe.total.ICMSTot.vICMS.valor = str(D(self.vr_icms_proprio))
        nfe.infNFe.total.ICMSTot.vICMSDeson.valor = str(D(self.vr_icms_desonerado))

        if nfe.infNFe.ide.idDest.valor == \
            IDENTIFICACAO_DESTINO_INTERESTADUAL and \
            nfe.infNFe.ide.indFinal.valor == \
            TIPO_CONSUMIDOR_FINAL_CONSUMIDOR_FINAL and \
            nfe.infNFe.dest.indIEDest.valor == \
            INDICADOR_IE_DESTINATARIO_NAO_CONTRIBUINTE:
            nfe.infNFe.total.ICMSTot.vICMSUFRemet.valor = str(
                D(self.vr_icms_estado_origem))

            nfe.infNFe.total.ICMSTot.vICMSUFDest.valor = str(D(self.vr_icms_estado_destino))
            nfe.infNFe.total.ICMSTot.vFCPUFDest.valor = str(D(self.vr_fcp))

        nfe.infNFe.total.ICMSTot.vBCST.valor = str(D(self.bc_icms_st))
        nfe.infNFe.total.ICMSTot.vST.valor = str(D(self.vr_icms_st))
        nfe.infNFe.total.ICMSTot.vProd.valor = str(D(self.vr_produtos))
        nfe.infNFe.total.ICMSTot.vFrete.valor = str(D(self.vr_frete))
        nfe.infNFe.total.ICMSTot.vSeg.valor = str(D(self.vr_seguro))
        nfe.infNFe.total.ICMSTot.vDesc.valor = str(D(self.vr_desconto))
        nfe.infNFe.total.ICMSTot.vII.valor = str(D(self.vr_ii))
        nfe.infNFe.total.ICMSTot.vIPI.valor = str(D(self.vr_ipi))
        nfe.infNFe.total.ICMSTot.vPIS.valor = str(D(self.vr_pis_proprio))
        nfe.infNFe.total.ICMSTot.vCOFINS.valor = str(D(self.vr_cofins_proprio))
        nfe.infNFe.total.ICMSTot.vOutro.valor = str(D(self.vr_outras))
        nfe.infNFe.total.ICMSTot.vNF.valor = str(D(self.vr_nf))
        nfe.infNFe.total.ICMSTot.vTotTrib.valor = str(D(self.vr_ibpt))

    def _monta_nfe_informacao_complementar(self, nfe):
        infcomplementar = self.infcomplementar or ''

        dados_infcomplementar = {
            'nf': self,
        }

        #
        # Crédito de ICMS do SIMPLES
        #
        if self.regime_tributario == REGIME_TRIBUTARIO_SIMPLES and \
                self.vr_icms_sn:
            if len(infcomplementar) > 0:
                infcomplementar += '\n'

            infcomplementar += \
                'Permite o aproveitamento de crédito de ' + \
                'ICMS no valor de R$ ${formata_valor(nf.vr_icms_sn)},' + \
                ' nos termos do art. 23 da LC 123/2006;'

        #
        # Valor do IBPT
        #
        if self.vr_ibpt:
            if len(infcomplementar) > 0:
                infcomplementar += '\n'

            infcomplementar += 'Valor aproximado dos tributos: ' + \
                               'R$ ${formata_valor(nf.vr_ibpt)} - fonte: IBPT;'

        #
        # ICMS para UF de destino
        #
        if nfe.infNFe.ide.idDest.valor == \
                IDENTIFICACAO_DESTINO_INTERESTADUAL and \
                nfe.infNFe.ide.indFinal.valor == \
                TIPO_CONSUMIDOR_FINAL_CONSUMIDOR_FINAL and \
                nfe.infNFe.dest.indIEDest.valor == \
                INDICADOR_IE_DESTINATARIO_NAO_CONTRIBUINTE:

            if len(infcomplementar) > 0:
                infcomplementar += '\n'

            infcomplementar += \
                'Partilha do ICMS de R$ ' + \
                '${formata_valor(nf.vr_icms_proprio)}% recolhida ' + \
                'conf. EC 87/2015: ' + \
                'R$ ${formata_valor(nf.vr_icms_estado_destino)} para o ' + \
                'estado de ${nf.partner_id.estado} e ' + \
                'R$ ${formata_valor(nf.vr_icms_estado_origem)} para o ' + \
                'estado de ${nf.empresa_id.estado}; Valor do diferencial ' + \
                'de alíquota: ' + \
                'R$ ${formata_valor(nf.vr_difal)};'

            if self.vr_fcp:
                infcomplementar += ' Fundo de combate à pobreza: R$ ' + \
                                   '${formata_valor(nf.vr_fcp)}'

        #
        # Aplica um template na observação
        #
        return self._renderizar_informacoes_template(
            dados_infcomplementar, infcomplementar)

    def _monta_nfe_informacao_fisco(self):
        infadfisco = self.infadfisco or ''

        dados_infadfisco = {
            'nf': self,
        }

        #
        # Aplica um template na observação
        #
        return self._renderizar_informacoes_template(
            dados_infadfisco, infadfisco)