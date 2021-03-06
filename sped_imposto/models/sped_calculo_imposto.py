# -*- coding: utf-8 -*-
# Copyright 2017 KMEE
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from __future__ import division, print_function, unicode_literals

import logging

from odoo import api, fields, models, _
import odoo.addons.decimal_precision as dp
from odoo.exceptions import ValidationError
from openerp.addons.l10n_br_base.tools.misc import calc_price_ratio
from odoo.addons.l10n_br_base.constante_tributaria import (
    MODELO_FISCAL_EMISSAO_PRODUTO,
    MODELO_FISCAL_EMISSAO_SERVICO,
    TIPO_PESSOA_FISICA,
)
from openerp.addons.l10n_br_base.models.sped_base import (
    SpedBase
)


class SpedCalculoImposto(SpedBase):
    """ Definie informações essenciais para as operações brasileiras

    Para entender como usar este modelo verifique os módulos:
        - sped_sale
        - sped_purchase

        Mas por via das dúvidas siga os passos:

        - Escolha o modelo de negócios que você deseja tornar faturável para o
        Brasil;
        - Crie um módulo com o padrõa l10n_MODELO ou sped_MODELO
        - Herde a classe que será no futuro o cabeçalho do documento fiscal
         da seguinte forma:

         from odoo.addons.sped_imposto.models.sped_calculo_imposto import (
            SpedCalculoImposto
         )

     class ModeloCabecalho(SpedCaluloImposto, models.Models):

        brazil_line_ids = fields.One2many(
            comodel_name='sale.order.line.brazil',
            inverse_name='order_id',
            string='Linhas',
            copy=True
        )

        def _get_date(self):
        # Return the document date Used in _amount_all_wrapper
            return self.date_order

        # Se o documento já criar a invoice injete os paramretros na criação.
        @api.multi
        def _prepare_invoice(self):
            vals = super(SaleOrder, self)._prepare_invoice()

            vals['sped_empresa_id'] = self.sped_empresa_id.id or False
            vals['sped_participante_id'] = \
                self.sped_participante_id.id or False
            vals['sped_operacao_produto_id'] = \
                self.sped_operacao_produto_id.id or False
            vals['sped_operacao_servico_id'] = \
                self.sped_operacao_servico_id.id or False
            vals['condicao_pagamento_id'] = \
                self.condicao_pagamento_id.id or False

            return vals

        @api.one
        @api.depends('order_line.price_total')
        def _amount_all(self):
            if not self.is_brazilian:
                return super(SaleOrder, self)._amount_all()
            return self._amount_all_brazil()
    """

    _abstract = False

    # TODO: Remover esta funçã;
    # Estamos validando a data dentro do calula impostos
    def _get_date(self):
        """
        Return the document date
        Used in get_info

        Override this method in your document like this:

        @api.multi
        def _get_date(self):
            date = super(MyClass, self)._get_date()

            # Your logic
            if self.your_date_field ... :
                return self.your_date_field
            return date

        NOTE: If the date of the tax camputation is important to you,
        don't forget to send this date to the invoice!
        """
        return fields.Date.context_today(self)

    def _amount_all_brazil(self):
        """ Tratamos os impostos brasileiros """
        #
        # amount_untaxed é equivalente ao valor dos produtos
        #
        if 'order_line' in self._fields:
            self.amount_untaxed = \
                sum(item.vr_produtos for item in self.order_line)
            #
            # amount_tax são os imposto que são somados no valor total da NF;
            # no nosso caso, não só impostos, mas todos os valores que entram
            # no total da NF: outras despesas acessórias, frete etc.
            # E, como o amount_total é o valor DA FATURA, não da NF, somamos este
            # primeiro, e listamos o valor dos impostos considerando valores
            # acessórios, e potencias retenções de imposto que estejam
            # reduzindo o valor
            #
            self.amount_total = \
                sum(item.vr_fatura for item in self.order_line)

            self.amount_tax = self.amount_total - self.amount_untaxed



    @api.model
    def _default_company_id(self):
        return self.env['res.company']._company_default_get(self._name)

    @api.depends('company_id', 'partner_id')
    def _compute_is_brazilian(self):
        for record in self:
            if record.company_id.country_id:
                if record.company_id.country_id.id == \
                        self.env.ref('base.br').id:
                    record.is_brazilian = True

                    if record.partner_id.sped_participante_id:
                        record.sped_participante_id = \
                            record.partner_id.sped_participante_id

                    if record.sped_empresa_id:
                        if (record.sped_participante_id.tipo_pessoa ==
                                TIPO_PESSOA_FISICA):
                            record.sped_operacao_produto_id = \
                                record.sped_empresa_id.\
                                operacao_produto_pessoa_fisica_id
                        else:
                            record.sped_operacao_produto_id = \
                                record.sped_empresa_id.operacao_produto_id
                        record.sped_operacao_servico_id = \
                            record.sped_empresa_id.operacao_servico_id
                    continue
            record.is_brazilian = False

    @api.one
    def _get_costs_value(self):
        """ Read the l10n_br specific functional fields. """
        if 'order_line' in self._fields:
            freight = costs = insurance = 0.0
            for line in self.order_line:
                freight += line.vr_frete
                insurance += line.vr_seguro
                costs += line.vr_outras
            self.vr_frete = freight
            self.vr_outras = costs
            self.vr_seguro = insurance

    @api.one
    def _set_amount_freight(self):
        if 'order_line' in self._fields:
            for line in self.order_line:
                if not self.vr_frete:
                    break
                line.write({
                    'vr_frete': calc_price_ratio(
                        line.vr_nf,
                        self.vr_frete,
                        line.order_id.amount_untaxed),
                })
        return True

    @api.one
    def _set_amount_insurance(self):
        if 'order_line' in self._fields:
            for line in self.order_line:
                if not self.vr_seguro:
                    break
                line.write({
                    'vr_seguro': calc_price_ratio(
                        line.vr_nf,
                        self.vr_seguro,
                        line.order_id.amount_untaxed),
                })
        return True

    @api.one
    def _set_amount_costs(self):
        if 'order_line' in self._fields:
            for line in self.order_line:
                if not self.vr_outras:
                    break
                line.write({
                    'vr_outras': calc_price_ratio(
                        line.vr_nf,
                        self.vr_outras,
                        line.order_id.amount_untaxed),
                })
        return True

    is_brazilian = fields.Boolean(
        string=u'Is a Brazilian?',
        compute='_compute_is_brazilian',
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        default=_default_company_id,
    )
    sped_empresa_id = fields.Many2one(
        comodel_name='sped.empresa',
        related='company_id.sped_empresa_id',
        string='Empresa',
        readonly=True,
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
    )
    sped_participante_id = fields.Many2one(
        comodel_name='sped.participante',
        string=u'Destinatário/Remetente'
    )
    sped_operacao_produto_id = fields.Many2one(
        comodel_name='sped.operacao',
        string=u'Operação Fiscal (produtos)',
        domain=[('modelo', 'in', MODELO_FISCAL_EMISSAO_PRODUTO)],
    )
    sped_operacao_servico_id = fields.Many2one(
        comodel_name='sped.operacao',
        string=u'Operação Fiscal (serviços)',
        domain=[('modelo', 'in', MODELO_FISCAL_EMISSAO_SERVICO)],
    )
    condicao_pagamento_id = fields.Many2one(
        comodel_name='account.payment.term',
        string='Condição de pagamento',
        domain=[('forma_pagamento', '!=', False)],
    )
    vr_frete = fields.Float(
        compute=_get_costs_value, inverse=_set_amount_freight,
        string='Frete', default=0.00, digits=dp.get_precision('Account'),
        readonly=True, states={'draft': [('readonly', False)]})
    vr_outras = fields.Float(
        compute=_get_costs_value, inverse=_set_amount_costs,
        string='Outros Custos', default=0.00,
        digits=dp.get_precision('Account'),
        readonly=True, states={'draft': [('readonly', False)]})
    vr_seguro = fields.Float(
        compute=_get_costs_value, inverse=_set_amount_insurance,
        string='Seguro', default=0.00, digits=dp.get_precision('Account'),
        readonly=True, states={'draft': [('readonly', False)]})

    @api.onchange('condicao_pagamento_id')
    def _onchange_condicao_pagamento_id(self):
        self.ensure_one()
        self.payment_term_id = self.condicao_pagamento_id

    @api.onchange('sped_participante_id')
    def _onchange_sped_participante_id(self):
        self.ensure_one()
        self.partner_id = self.sped_participante_id.partner_id

    @api.multi
    def _prepare_sped(self, operacao_id):
        """
        Prepare the dict of values to create the new fiscal for an invoice.

        This method may be overridden to implement custom fiscal generation

        (making sure to call super() to establish a clean extension chain).
        """
        self.ensure_one()
        infcomplementar = self.comment if 'comment' in self._fields \
            else self.note or ''

        sped_vals = {
            'empresa_id': self.sped_empresa_id.id,
            'operacao_id': operacao_id.id,  # FIXME
            'participante_id': self.sped_participante_id.id,
            'payment_term_id': self.condicao_pagamento_id and \
                               self.condicao_pagamento_id.id or False,
            'modelo': operacao_id.modelo,
            'emissao': operacao_id.emissao,
            'natureza_operacao_id': operacao_id.natureza_operacao_id.id,
            'infcomplementar': infcomplementar,
            'journal_id': self.journal_id.id if 'journal_id' in \
                                                self._fields else False,
            'serie': operacao_id.serie,
            # 'duplicata_ids': ,
            # 'pagamento_ids': ,
            # 'transportadora_id': ,
            # 'volume_ids': ,
            # 'name': self.client_order_ref or '',
            # 'origin': self.name,
            # 'type': 'out_invoice',
            # 'account_id':
            #   self.partner_invoice_id.property_account_receivable_id.id,
            # 'partner_shipping_id': self.partner_shipping_id.id,k
            # 'user_id': self.user_id and self.user_id.id,
            # 'team_id': self.team_id.id
        }

        return sped_vals
