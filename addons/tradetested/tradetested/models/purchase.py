# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.tools.float_utils import float_is_zero, float_compare
from odoo.tools.safe_eval import safe_eval

from datetime import datetime
import time
import common

READONLY_STATES = {
    'purchase': [('readonly', True)],
    'done': [('readonly', True)],
    'cancel': [('readonly', True)],
}


class purchase_order(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'obj.watchers.base', 'base.activity']

    partner_id = fields.Many2one('res.partner', string='Supplier', required=True, states=READONLY_STATES, change_default=True, track_visibility='onchange')
    date_planned = fields.Date(string='Expected Date', compute='_compute_date_planned', required=True, index=True, oldname='minimum_planned_date')
    order_line = fields.One2many('purchase.order.line', 'order_id', 'Order Lines', states={'purchase': [('readonly', False)], 'done': [('readonly', False)]})
    is_order_line_readonly = fields.Boolean(compute='_is_order_line_readonly', string="Readonly")
    landing_date = fields.Date('Landing Date')
    case_exists = fields.Boolean(compute='_case_exists', string="Case Exists")
    related_exists = fields.Boolean(compute='_related_exists', string='Related Draft PO Exists')
    sale_id = fields.Many2one('sale.order', 'Related Sale Order')
    partner_ref = fields.Char('Supplier Reference', states={'done': [('readonly', True)]}, size=64, )
    payment_ids = fields.One2many('purchase.order.payment', 'purchase_order_id', 'Payment')
    payments_total = fields.Float(compute='_cal_totals', string='Total payments', store=True)
    refunds_total = fields.Float(compute='_cal_totals', string='Total refunds', store=True)
    balance_remaining = fields.Float(compute='_cal_totals', string='Balance remaining', store=True)
    payments_total_less_refunds = fields.Float(compute='_cal_totals', string='Payments balance', help="Total payments less refunds.", store=True)
    total_currency = fields.Float('Total')
    supplier_terms = fields.Char(related='partner_id.supplier_terms', type="char", string='Supplier Terms', readonly=True)
    held = fields.Boolean('Held')
    held_reason = fields.Selection([('Order Point', 'Order Point'), ('Out of Stock', 'Out of Stock'), ('Other', 'Other')], string="Held Reason")
    held_date = fields.Date('Held Date')
    container_number = fields.Char('Container Number')
    currency_id = fields.Many2one('res.currency', 'Currency', required=True, states=READONLY_STATES, default=lambda self: self.env.user.company_id.currency_id)
    xero_id = fields.Char('Xero ID')
    xero_export_at = fields.Datetime('Exported at')
    is_shipped = fields.Boolean(compute="_compute_is_shipped", store=False)

    @api.multi
    def _is_order_line_readonly(self):
        user_dispatch_cord = self.env.user.has_group('tradetested.group_stock_dispatch')
        for po in self:
            if po.state in ['draft', 'sent'] or user_dispatch_cord:
                po.is_order_line_readonly = False
            else:
                po.is_order_line_readonly = True

    @api.multi
    def _case_exists(self):
        for po in self:
            po.case_exists = any([case.id for case in po.partner_id.supplier_case_ids if case.state not in ['cancel', 'done']])

    @api.multi
    def _related_exists(self):
        for po in self:
            po.related_exists = self.search_count([('id', '!=', po.id), ('partner_id', '=', po.partner_id.id), ('state', '=', 'draft'), ('sale_id', '=', False)])

    @api.multi
    @api.depends('payment_ids', 'payment_ids.amount', 'payment_ids.method', 'payment_ids.purchase_order_id')
    def _cal_totals(self):
        for po in self:
            payments = 0.0
            refunds = 0.0
            for tran in po.payment_ids:
                if tran.type == 'payment':
                    payments += tran.amount
                elif tran.type == 'refund':
                    refunds += tran.amount
            po.update({
                'payments_total': payments,
                'refunds_total': refunds,
                'payments_total_less_refunds': payments - refunds,
                'balance_remaining': po.amount_total - (payments - refunds)
            })

    @api.multi
    def add_payment(self):
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'purchase.order.payment',
            'target': 'new',
            'context': {'purchase_order_id': self.id}
        }

    @api.multi
    def _add_supplier_to_product(self):
        # Disabled adding Products to supplier
        pass

    # OnChange
    @api.onchange('partner_id')
    def onchange_partner_id(self):
        resp = super(purchase_order, self).onchange_partner_id()
        if self.partner_id:
            self.supplier_terms = self.partner_id.supplier_terms

    @api.onchange('held', 'held_reason')
    def onchange_held(self):
        if not self.held:
            self.held_date = False
            self.held_reason = False

    @api.onchange('company_id')
    def onchange_company_id(self):
        if self.company_id:
            picking_types = self.env['stock.picking.type'].search([('name', '=', 'Receipts'), ('warehouse_id.name', '=', self.company_id.name)])
            if picking_types:
                self.picking_type_id = picking_types[0].id
            picking_types = self.env['stock.picking.type'].search([('name', '=', 'Receipts'), ('warehouse_id.company_id', '=', self.company_id.id)])
            return {'domain': {'picking_type_id': [('id', 'in', [pt.id for pt in picking_types])]}}


class purchase_order_line(models.Model):
    _inherit = 'purchase.order.line'
    _order = 'sequence'

    sequence = fields.Integer('Sequence')
    product_uom = fields.Many2one('product.uom', 'Product Unit of Measure', required=True, default=lambda self: self.env.ref('product.product_uom_unit'))
    sale_order_line_id = fields.Many2one('sale.order.line', 'Sale Order Line')
    qty_received = fields.Float(compute='_compute_qty_received', string="Received Qty", store=True, digits=(11, 0))
    is_shipped = fields.Boolean(compute='_compute_qty_received', string="Received", store=True)
    date_planned = fields.Date(string='Scheduled Date', required=True, index=True)

    @api.multi
    def _compute_qty_received(self):
        super(purchase_order_line, self)._compute_qty_received()
        for line in self:
            line.is_shipped = (line.product_qty - line.qty_received) <= 0

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        if self._context.get('default_product_id') and view_type == 'form':
            self._context['form_view_ref'] = 'purchase.purchase_order_line_form2'
        return super(purchase_order_line, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=False)

    @api.multi
    def open_po(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'purchase.order',
            'res_id': self.order_id.id,
            'target': 'current',
            'context': {}
        }


class purchase_order_payment(models.Model):
    _name = "purchase.order.payment"

    purchase_order_id = fields.Many2one('purchase.order', 'Purchase Order', required=True, ondelete='cascade', index=True, readonly=True)
    type = fields.Selection((('payment', 'Payment'), ('refund', 'Refund')), 'Type', default='payment')
    date = fields.Datetime('Created', default=lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'))
    user_id = fields.Many2one('res.users', 'Created By')
    method = fields.Selection(common.payment_methods, 'Method', required=True)
    amount = fields.Float('Amount', digits=(11, 2))
    comment = fields.Char('Comment', size=2048)

    @api.model
    def default_get(self, fields):
        rec = super(purchase_order_payment, self).default_get(fields)
        if 'purchase_order_id' in self._context:
            bal_remaining = self.env['purchase.order'].browse(self._context['purchase_order_id']).balance_remaining
            if bal_remaining > 0:
                rec['amount'] = bal_remaining
            rec['purchase_order_id'] = self._context['purchase_order_id']
        return rec

    @api.multi
    def save(self):
        return True
