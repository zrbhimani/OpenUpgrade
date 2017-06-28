# -*- coding: utf-8 -*-

from odoo import tools, api, fields, models, _, tools
from odoo.tools import float_compare, DEFAULT_SERVER_DATE_FORMAT
from odoo.addons import decimal_precision as dp
from odoo.exceptions import UserError

from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta

import time
import common
import simplejson as json
import tt_fields
import logging
import re
import requests_oauthlib
import uuid

_logger = logging.getLogger(__name__)


class sale_order_line(models.Model):
    _inherit = 'sale.order.line'
    _order = 'order_id desc, sequence, id'

    order_state = fields.Selection(related='order_id.state', selection=common.SALE_STATES, string='Order Status')
    date_order = fields.Date(related='order_id.date_order', string='Order Date')
    order_balance = fields.Monetary(related='order_id.balance_remaining', string='Balance')
    order_warehouse_id = fields.Many2one(related='order_id.warehouse_id', string='Shop')
    order_channel = fields.Selection(related='order_id.channel', selection=common.sale_channels, string='Sale channel', store=True)
    order_paid = fields.Boolean(related='order_id.paid', string='Paid', store=True)
    product_po_expected_date = fields.Date(related='product_id.po_expected_date', string='PO Expected Date')

    stock_alloc = fields.Selection(compute='_stock_alloc', selection=[('Y', 'Yes'), ('N', 'No')], string='Outgoing')
    stock_indicator = fields.Char(compute='_stock_indicator', string="Stock Indicator")
    stock_indicator_tooltip = fields.Text(compute='_stock_indicator', string="Stock Indicator")
    qty_returned = fields.Float('Quantity Returned')
    cust_expected_date = fields.Date('Customer Expected Date')
    delayed = fields.Boolean(compute='_delayed', string='Delayed')  # , search='_search_delayed'

    # Compute
    def _search_delayed(self, operator, value):
        if operator == '=' and value:
            self._cr.execute("""SELECT
                       sol.id,
                       pp.id,
                       sol.cust_expected_date
                   FROM
                       sale_order_line sol,
                       product_product pp,
                       product_template pt,
                       sale_order so
                   WHERE
                       sol.product_id = pp.id AND
                       sol.order_id = so.id AND
                       pp.product_tmpl_id = pt.id AND
                       so.state not in ('cancel','quote','done') AND
                       pt.type='product' AND
                       coalesce(sol.qty_delivered, 0) < sol.product_uom_qty
               """)
            lines = [(x[0], x[1], x[2]) for x in self._cr.fetchall()]
            prod_stock = self.pool['product.product']._product_available_cache(self._cr, self._uid, [x[1] for x in lines])
            ret_ids = []
            for line in lines:
                if prod_stock[line[1]]['saleable_qty'] < 0:
                    ret_ids.append(line[0])
                elif line[2]:
                    po_date = self.pool['product.product']._po_expected_date_qty_cache(self._cr, self._uid, [line[1]])[line[1]]['po_expected_date']
                    if po_date and datetime.strptime(line[2], '%Y-%m-%d') > datetime.strptime(po_date, '%Y-%m-%d'):
                        ret_ids.append(line[0])
            return [('id', 'in', ret_ids)]

    @api.multi
    def _delayed(self):
        for line in self:
            if line.product_id.type != 'product':
                continue
            if line.order_id.state not in ['cancel', 'quote', 'done'] and line.qty_delivered < line.product_uom_qty and (line.cust_expected_date and line.cust_expected_date > line.product_id.po_expected_date):
                line.delayed = True
            elif line.product_id.saleable_qty < 0:
                line.delayed = True

    @api.multi
    def _prepare_order_line_procurement(self, group_id=False):
        self.ensure_one()
        return {
            'name': self.name,
            'origin': self.order_id.name,
            'date_planned': datetime.strptime(self.order_id.date_order, DEFAULT_SERVER_DATE_FORMAT) + timedelta(days=self.customer_lead),
            'product_id': self.product_id.id,
            'product_qty': self.product_uom_qty,
            'product_uom': self.product_uom.id,
            'company_id': self.order_id.company_id.id,
            'group_id': group_id,
            'sale_line_id': self.id,

            'location_id': self.order_id.partner_shipping_id.property_stock_customer.id,
            'route_ids': self.route_id and [(4, self.route_id.id)] or [],
            'warehouse_id': self.order_id.warehouse_id and self.order_id.warehouse_id.id or False,
            'partner_dest_id': self.order_id.partner_shipping_id.id,

            # 'property_ids': [(6, 0, self.property_ids.ids)],
        }

    @api.multi
    def _stock_indicator(self):
        today = common.convert_tz(datetime.today(), 'Pacific/Auckland').replace(tzinfo=None)
        for line in self:
            indicator = ''
            tooltip = ''
            if line.order_id.state not in ['quote', 'cancel', 'done']:
                if line.product_id and line.product_id.type == 'product':
                    tooltip = ''
                    if line.cust_expected_date:
                        tooltip = 'Customer Expected Date: %s\n' % datetime.strptime(line.cust_expected_date, '%Y-%m-%d').strftime('%d/%m/%Y')

                    tooltip += 'On Hand: %s\nIncoming: %s\nOutgoing: %s\nForecasted: %s\nSaleable: %s' % (
                        line.product_id.qty_available, line.product_id.incoming_qty, line.product_id.outgoing_qty, line.product_id.virtual_available, line.product_id.saleable_qty)

                    if line.product_id.po_expected_date:
                        tooltip += '\nPO Expected Date: %s\nPO Expected Qty: %.0f' % (line.product_id.po_expected_date, line.product_id.po_expected_qty)

                    old_line_qty = 0
                    if line.order_id.state == 'draft' and line.order_id.payments_total_less_refunds <= 0 and (today - datetime.strptime(line.order_id.date_order, '%Y-%m-%d')).days > 14:
                        old_line_qty = line.product_uom_qty

                    if line.product_id.qty_available <= 0:
                        indicator = 'r'
                    else:
                        if line.product_id.saleable_qty - old_line_qty >= 0:
                            indicator = 'g'
                        else:
                            indicator = 'y'

            line.stock_indicator = indicator + ',' + tooltip

    @api.multi
    def _stock_alloc(self):
        today = common.convert_tz(datetime.today(), 'Pacific/Auckland').replace(tzinfo=None)
        for line in self:
            alloc = ''
            move_exists = self.env['stock.move'].search_count([('state', 'in', ['assigned', 'confirmed', 'waiting']), ('picking_id', '!=', False)])  # ('sale_line_id', '=', line.id)
            if move_exists:
                alloc = 'Y'
            elif line.order_id.state == 'draft':
                if line.order_id.payments_total_less_refunds > 0 or (today - datetime.strptime(line.order_id.date_order, '%Y-%m-%d')).days <= 14:
                    alloc = 'Y'
                else:
                    alloc = 'N'
            line.stock_alloc = alloc

    @api.multi
    @api.onchange('product_id')
    def product_id_change(self):

        if not self.product_id:
            return {'domain': {'product_uom': []}}

        vals = {}
        domain = {'product_uom': [('category_id', '=', self.product_id.uom_id.category_id.id)]}

        if not self.product_uom or (self.product_id.uom_id.category_id.id != self.product_uom.category_id.id):
            vals['product_uom'] = self.product_id.uom_id

        product = self.product_id.with_context(
            lang=self.order_id.partner_id.lang,
            partner=self.order_id.partner_id.id,
            quantity=self.product_uom_qty,
            date=self.order_id.date_order,
            pricelist=self.order_id.pricelist_id.id,
            uom=self.product_uom.id
        )
        name = product.name
        self._compute_tax_id()

        price_unit = product.list_price
        if product.special_price and product.special_price > 0:
            if (not product.special_to_date) or (datetime.strptime(product.special_to_date, '%Y-%m-%d %H:%M:%S') > datetime.now()):
                if (not product.special_from_date) or (datetime.strptime(product.special_from_date, '%Y-%m-%d %H:%M:%S') < datetime.now()):
                    price_unit = product.special_price

        if self.order_id.pricelist_id and self.order_id.partner_id:
            price_unit = self.env['account.tax']._fix_tax_included_price(price_unit, product.taxes_id, self.tax_id)

        self.update({'name': name, 'price_unit': price_unit})
        return {'domain': domain}

    @api.onchange('product_id', 'product_uom_qty', 'product_uom', 'route_id')
    def _onchange_product_id_check_availability(self):
        if not self.product_id or not self.product_uom_qty or not self.product_uom:
            self.product_packaging = False
            return {}

        if self.product_id.type == 'product':
            if self.product_id.saleable_qty <= 0:
                msg = "Warning: This Product Is Out Of Stock.\nOn Hand: %s units\nSaleable: %s units" % (self.product_id.qty_available, self.product_id.saleable_qty)
                if self.product_id.po_expected_qty:
                    msg += '\n\nExpected Date: %s\nExpected Quantity: %.0f' % (datetime.strptime(self.product_id.po_expected_date, '%Y-%m-%d').strftime('%d %b %Y'), self.product_id.po_expected_qty)
                warning_mess = {
                    'title': _('Out of Stock!'),
                    'message': msg
                }
                return {'warning': warning_mess}
        return {}

    @api.onchange('product_uom', 'product_uom_qty')
    def product_uom_change(self):
        # Override default method - it's messing with special price
        # Nothing to do here - because we are not using multiple UoM
        return


    # Action
    @api.multi
    def open_product(self):
        sobj = self[0]
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'product.product',
            'res_id': sobj.product_id.id,
            'target': 'current',
        }

    @api.multi
    def open_sale_order(self):
        sobj = self[0]
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'sale.order',
            'res_id': sobj.order_id.id,
            'context': self._context,
            'target': 'new',
            'options': {'initial_mode': 'view'},
        }

    @api.multi
    def set_cust_expected_date(self):
        for line in self:
            line.cust_expected_date = line.product_po_expected_date

    @api.multi
    def remove_cust_expected_date(self):
        for line in self:
            line.cust_expected_date = False

    #ORM
    @api.multi
    def unlink(self):
        for line in self:
            data = {'res_model': 'sale.order', 'user_id': self._uid, 'date': datetime.now(), 'activity': 'Order Lines : Deleted', 'value_after': '', 'res_id': line.order_id.id}
            if line.product_id:
                data['value_before'] = (line.product_id.default_code or line.product_id.name) + ' X ' + str(int(line.product_uom_qty)) + ' @ ' + '%.2f' % line.price_unit
            else:
                data['value_before'] = line.name + ' X ' + str(int(line.product_uom_qty)) + ' @ ' + '%.2f' % line.price_unit
            self.env['res.activity.log'].create(data)
        return super(sale_order_line, self).unlink()

    @api.model
    def create(self, vals):
        data = {'user_id': self._uid, 'date': datetime.now(), 'res_id': vals['order_id'], 'res_model': 'sale.order', 'activity': 'Order Lines : Added', 'value_before': ''}
        if vals.get('product_id'):
            prod = self.env['product.product'].browse(vals['product_id'])
            data['value_after'] = (prod.default_code or prod.name) + ' X ' + str(int(vals['product_uom_qty'])) + ' @ ' + '%.2f' % (vals.get('price_unit') or float(0))
        else:
            data['value_after'] = '"' + vals['name'] + '"' + ' X ' + str(int(vals['product_uom_qty'])) + ' @ ' + '%.2f' % vals['price_unit']
        self.env['res.activity.log'].create(data)
        if vals.get('product_id'):
            product = self.env['product.product'].browse(vals['product_id'])
            if product.saleable_qty <= 0 and product.po_expected_date:
                vals['cust_expected_date'] = product.po_expected_date
        return super(sale_order_line, self).create(vals)

    @api.multi
    def write(self, vals):
        for line in self:
            if ('product_id' in vals) or ('product_uom_qty' in vals) or ('price_unit' in vals):
                data = {'user_id': self._uid, 'date': datetime.now(), 'activity': 'Order Lines : Updated', 'res_id': line.order_id.id, 'res_model': 'sale.order'}
                data['value_before'] = (line.product_id.default_code or (line.product_id.name or line.name)) + ' X ' + str(int(line.product_uom_qty)) + ' @ ' + '%.2f' % line.price_unit

                if 'product_id' in vals:
                    line_prod = self.env['product.product'].browse(vals['product_id'])
                else:
                    line_prod = line.product_id

                if 'product_uom_qty' in vals:
                    line_qty = vals['product_uom_qty']
                else:
                    line_qty = line.product_uom_qty

                if 'price_unit' in vals:
                    line_price = vals['price_unit']
                else:
                    line_price = line.price_unit

                data['value_after'] = (line_prod.default_code or (line_prod.name or line_prod.name)) + ' X ' + str(int(line_qty)) + ' @ ' + '%.2f' % line_price
                self.env['res.activity.log'].create(data)

        return super(sale_order_line, self).write(vals)

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        if 'order_so_line' in self._context:
            order = self._context['order_so_line']
        return super(sale_order_line, self).search(args, offset, limit, order, count)


class sale_order(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'mail.thread', 'obj.watchers.base', 'ir.needaction_mixin', 'base.activity']
    _order = 'date_order desc, name desc'

    @tools.ormcache()
    def _selection_order_status(self):
        return [(cat['code'], cat['name']) for cat in self.env['sale.order.held.category'].search([])]

    @api.model
    def _default_warehouse_id(self):
        company = self.env.user.company_id.id
        warehouse_ids = self.env['stock.warehouse'].search([('company_id', '=', company)], limit=1)
        return warehouse_ids

    is_sale_manager = fields.Boolean(compute='_is_sale_manager', string="Sales Manager")

    case_ids = fields.One2many('crm.helpdesk', 'order_id', string='Cases')
    pending_cases = fields.Boolean(compute='_get_cases', string='Open/Pending Cases')
    case_note = fields.Char(compute='_get_cases', string="Case Note")
    open_cases = fields.Integer(compute='_get_cases', string="Open Cases", search='_search_open_cases')
    product_cases = fields.Integer(compute='_get_cases', string="Product Cases", search='_search_product_cases')

    phone = fields.Char('Phone', size=64)
    phone2 = fields.Char('Other Ph.', size=64)
    delivery_instructions = fields.Text('Delivery Instructions', size=2048)
    tt_company_name = fields.Char('Company', size=64)

    related_orders = fields.One2many('sale.order', compute='_related_orders', string='Related Orders')
    sale_order_id = fields.Many2one('sale.order', compute='_sale_order_id', string='Sale Order', store=True)

    order_held = fields.Boolean('Held')
    dispatch_exception = fields.Boolean('Dispatch Exception')
    exception_reason_ids = fields.Many2many('sale.order.exception', 'rel_sale_order_exception', 'order_id', 'exception_id', 'Exception Reason')
    order_status = fields.Selection(_selection_order_status, string='Held Category')
    future_date = fields.Date('Future Date')
    future_order = fields.Boolean(compute='_future_order', string="Future Order", search='_search_future_order')

    amount_untaxed = fields.Monetary(string='Untaxed Amount', store=True, readonly=True, compute='_amount_all', track_visibility=False)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='_amount_all', track_visibility=False)
    amount_total = fields.Monetary(string='Total', store=True, readonly=True, compute='_amount_all', track_visibility=False)

    balance_remaining = fields.Monetary(compute='_totals', string='Balance remaining', help="Total due minus any payments.", store=True)
    payments_total = fields.Monetary(compute='_totals', string='Total payments', help="Total payments.", store=True)
    refunds_total = fields.Monetary(compute='_totals', string='Total refunds', help="Total refunds.", store=True)
    payments_total_less_refunds = fields.Monetary(compute='_totals', string='Payments Balance', help="Total payments less refunds.", store=True)
    paid = fields.Boolean(compute='_totals', string='Paid', help="Sale Order Paid", store=True)

    order_line = fields.One2many('sale.order.line', 'order_id', string='Order Lines', readonly=False)

    is_line_readonly = fields.Boolean(compute='_is_line_readonly', string="Readonly")
    ticket_ids = fields.One2many('zendesk.ticket', compute='_ticket_ids', string="Tickets")

    date_payment_reminder = fields.Datetime('Payment reminder email sent')
    date_payment_reminder_2 = fields.Datetime('Payment reminder 2 email sent')
    email_status_update = fields.Datetime('Email status update')
    cancel_case_date = fields.Datetime('Cancellation Case Created')
    mailchimp_export_date = fields.Datetime('Exported to Mailchimp')

    amount_total_nzd = fields.Float(compute='_amount_total_nzd', digits=dp.get_precision('Account'), string='Total NZD', store=True)

    ship_tt_company_name = fields.Char('Company', size=64)
    ship_street = fields.Char('Street', size=128)
    ship_street2 = fields.Char('Street2', size=128)
    ship_zip = fields.Char('Zip', change_default=True, size=24)
    ship_city = fields.Char('City', size=128)
    ship_state_id = fields.Many2one("res.country.state", string='State')
    ship_country_id = fields.Many2one('res.country', string='Country')
    last_ship_address = fields.Boolean('Last Ship Address')
    change_in_address = fields.Boolean('Change in address fields')
    signature_opt_out = fields.Boolean('Signature Not Req.', default=False)

    marketing_method_id = fields.Many2one('sale.order.marketing.method', 'Marketing Method')

    quote_count = fields.Integer(compute='_count_quotation', string="Draft Orders")

    is_rural_delivery = fields.Boolean(compute='_is_rural_delivery', string="Is Rural Delivery", store=True)
    frequency = fields.Selection(compute='_frequency', selection=[('first', 'First Time'), ('repeat', 'Repeat')], string="Customer Frequency", store=True)
    last_partner_id = fields.Many2one('res.partner', 'Last Partner')

    visible_confirm_sale = fields.Boolean(compute='_visible_confirm_sale', string="Confirm Sale")
    date_productfaq_email = fields.Datetime('Product Faq Email')
    oos_case_date = fields.Datetime('OOS Case Created ')

    is_tax_missing = fields.Boolean(compute='_is_tax_missing', string="Tax Missing", store=True)
    order_weight = fields.Float(compute='_order_weight', string="Order Weight", store=True)

    purchase_order_ids = fields.One2many('purchase.order', 'sale_id', 'Purchase Orders')

    can_merge = fields.Char(compute='_can_merge', string="Can Merge")

    total_quantity = fields.Integer(compute='_get_total_quantity', string="Quantity")
    date_done = fields.Date('Date Done')
    date_confirm = fields.Date('Confirmation Date', readonly=True, index=True, help="Date on which sales order is confirmed.", copy=False)

    license_number = fields.Char('License Number', size=9)
    license_version = fields.Char('Version', size=3)

    magento_create_date = fields.Datetime('Magento Create Date')
    magento_update_date = fields.Datetime('Magento Update Date')
    magento_order_number = fields.Integer('Magento Order Number')

    channel = fields.Selection(common.sale_channels, 'Sale channel', default='not_defined')
    pick_date = fields.Datetime('Pick date')
    ship_date = fields.Datetime('Ship date')
    ship_via = fields.Char('Ship via', size=255)
    tracking_number = fields.Char('Tracking number', size=255)
    date_followup_email = fields.Datetime('Email followup email sent')
    date_payment_confirm = fields.Datetime('Email payment confirm email sent')

    date_pickup_email = fields.Datetime('Email pickup email sent')
    date_feedback = fields.Datetime('Feedback Placed')
    trademe_purchase_id = fields.Integer('Trademe purchase id', required=False)
    trademe_listing_id = fields.Integer('Trademe listing id', required=False)
    trademe_username = fields.Char('Trademe Username', size=256)
    trademe_sale_type = fields.Selection(common.trademe_sale_types, 'Trademe sale type', required=False, default='na')
    trademe_pay_now_purchase = fields.Boolean('Trademe pay-now purchase?', required=False, default=False)
    fm_order_number = fields.Char('Filemaker Invoice Number', size=64)

    message_common_ids = fields.One2many('mail.message', compute='_message_common_ids', string='Messages', context={'THISISIT': 1})
    sale_order_payment_id = fields.One2many('sale.order.payment', 'sale_order_id', 'Payment')

    date_order = fields.Date(string='Date', required=True, readonly=True, index=True, states={'draft': [('readonly', False)], 'sent': [('readonly', False)]}, copy=False, default=fields.Date.context_today)
    guid = tt_fields.Uuid('GUID')
    state = fields.Selection(common.SALE_STATES, string='Status', readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Shop', required=True, readonly=True, states={'draft': [('readonly', False)], 'sent': [('readonly', False)]}, default=_default_warehouse_id)

    delivery_price = fields.Float(string='Estimated Delivery Price', compute='_compute_delivery_price', store=True)
    carrier_id = fields.Many2one("delivery.carrier", string="Delivery Method", help="Fill this field if you plan to invoice the shipping based on picking.")
    invoice_shipping_on_delivery = fields.Boolean(string="Invoice Shipping on Delivery")

    sale_return_ids = fields.One2many('sale.order.return', 'order_id', 'Return Orders')
    fraud_score_details = fields.Serialized(compute='_fraud_score_details', string='Fraud Score Details')
    delivery_order_status = fields.Selection(compute='_delivery_order_status', selection=common.DELIVERY_STATES, string="Delivery Status")

    display_address = fields.Text(compute='_display_address', string="Display Address")

    _sql_constraints = [
        ('guid_uniq', 'unique(guid)', 'Unique GUID is required'),
    ]

    _groups_ref = {}

    def groups_ref(self):
        for grp in ['tradetested.group_shop_salesperson', 'tradetested.group_stock_dispatch']:
            self._groups_ref[grp] = self.env['ir.model.data'].sudo().xmlid_to_res_id(grp)

    # COMPUTE
    @api.multi
    def _delivery_order_status(self):
        for order in self:
            if order.picking_ids:
                picking = order.picking_ids[0]
                # del_state = picking.state
                # order.delivery_order_status = del_state
                return_picking = ''
                # if picking.type == 'in':
                #     return_picking = ' (Return)'

    # TODO check picking.type

    @api.multi
    def _delivery_unset(self):
        self.env['sale.order.line'].search([('order_id', 'in', self.ids), ('is_delivery', '=', True)]).unlink()

    @api.multi
    def _ticket_ids(self):
        for so in self:
            if so.partner_id.email:
                args = ['|', ('partner_id', '=', so.partner_id.id), ('from_email', '=', so.partner_id.email)]
            elif so.partner_id:
                args = [('partner_id', '=', so.partner_id.id)]
            else:
                continue
            so.ticket_ids = [t.id for t in self.env['zendesk.ticket'].search(args)]

    @api.multi
    def _message_common_ids(self):
        for order in self:
            order.message_common_ids = [m.id for m in self.env['mail.message'].search(
                ['|', '&', ('model', '=', 'sale.order'), ('res_id', '=', order.id), '&', '&', '|', ('body', '!=', ''), ('type', '=', 'comment'), ('model', 'in', ['stock.picking.out', 'stock.picking']),
                 ('res_id', 'in', [p.id for p in order.picking_ids])])]

    @api.multi
    def _can_merge(self):
        for order in self:
            merge = False
            if order.state == 'draft':
                rel_quotations = [r_order.balance_remaining for r_order in order.related_orders if r_order.state == 'draft']
                if rel_quotations:
                    if float_compare(max(rel_quotations), 0, 2) > 0:
                        merge = "Possible"
                    else:
                        merge = "Yes"
            order.can_merge = merge

    @api.multi
    def _related_orders(self):
        for so in self:
            if so.partner_id and so.partner_id.email:
                args = [('partner_id.email', '=', so.partner_id.email)]
                if so.id:
                    args.append(('id', '!=', so.id))
                so.related_orders = [s.id for s in self.search(args)]

    @api.multi
    def _get_cases(self):
        res = {}
        for so in self:
            case_note = pending_cases = False
            count_open = self.env['crm.helpdesk'].search_count([('state', 'in', ['open', 'draft']), ('order_id', '=', so.id)])
            count_pending = self.env['crm.helpdesk'].search_count([('state', '=', 'pending'), ('order_id', '=', so.id)])
            count_prod_cases = self.env['crm.helpdesk'].search_count([('order_id', '=', so.id), '|', ('categ_id.product_case', '=', True), ('resolution_ids.product_case', '=', True)])

            case_note = ''
            if count_open == 1:
                case_note = 'There is %s open case' % (count_open)
            elif count_open > 1:
                case_note = 'There are %s open cases' % (count_open)

            if count_pending > 0:
                if count_open > 0:
                    case_note += ' and'
                    if count_pending == 1:
                        case_note += ' %s pending case' % (count_pending)
                    elif count_pending > 1:
                        case_note += ' %s pending cases' % (count_pending)

                elif count_open == 0:
                    if count_pending == 1:
                        case_note += 'There is %s pending case' % (count_pending)
                    elif count_pending > 1:
                        case_note += 'There are %s pending cases' % (count_pending)

            if count_pending or count_open:
                case_note += ' relating to this sales order'
                pending_cases = True

            so.update({
                'case_note': case_note,
                'pending_cases': pending_cases,
                'open_cases': count_open,
                'product_cases': count_prod_cases
            })
        return res

    @api.multi
    def _visible_confirm_sale(self):
        print "VISIBLE CONFIRM  "
        # if not self._groups_ref:
        #     self.groups_ref()
        # for order in self:
        #     if (self._groups_ref['group_stock_dispatch'] in self.env.user.groups_id.ids) or \
        #             (order.carrier_id.name == 'Pickup' and (self.env.user.team_id.name == 'Showroom' or self._groups_ref['group_shop_salesperson'] in self.env.user.groups_id.ids)):
        #         order.visible_confirm_sale = True

    @api.multi
    def _is_sale_manager(self):
        allowed_groups = self.env['res.groups'].search([('name', 'in', ['Dispatch Coordinator', 'Manager']), ('category_id.name', 'in', ['Sales', 'Warehouse'])])
        user_groups = [g.id for g in self.env.user.groups_id]
        inter = list(set(allowed_groups).intersection(set(user_groups)))
        if inter:
            for rec in self:
                rec.is_sale_manager = True
        else:
            for rec in self:
                rec.is_sale_manager = False

    @api.multi
    @api.depends('name')
    def _sale_order_id(self):
        for so in self:
            so.sale_order_id = so.id

    @api.multi
    def _future_order(self):
        today = datetime.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d')
        for order in self:
            if order.order_status in ['future_delivery', 'future_payment'] and order.future_date and datetime.strptime(order.future_date, '%Y-%m-%d') > today:
                print order.order_status, "order status"
                order.future_order = True

    @api.depends('order_line.price_total', 'sale_order_payment_id')
    def _totals(self):
        for so in self:
            payments = 0.0
            refunds = 0.0
            for tran in so.sale_order_payment_id:
                if tran.type == 'payment':
                    payments += round(tran.amount, 2)
                elif tran.type == 'refund':
                    refunds += round(tran.amount, 2)

            payments_total_less_refunds = round(payments - refunds, 2)
            so.update({
                'payments_total': payments,
                'refunds_total': refunds,
                'payments_total_less_refunds': payments_total_less_refunds,
                'balance_remaining': round(so.amount_total - (payments - refunds), 2),
                'paid': True if (float_compare(payments_total_less_refunds, so.amount_total, 2) >= 0) else False
            })

    @api.multi
    def _is_line_readonly(self):
        user_groups = [x.name for x in self.env.user.groups_id]
        for so in self:
            if so.state == 'draft' or 'Dispatch Coordinator' in user_groups:
                so.is_line_readonly = False
            else:
                so.is_line_readonly = True

    @api.multi
    @api.depends('amount_total', 'date_order')
    def _amount_total_nzd(self):
        nzd_currency = self.env['res.currency'].search([('name', '=', 'NZD')])
        for order in self:
            order.amount_total_nzd = order.pricelist_id.currency_id.with_context({'date': order.date_order}).compute(order.amount_total, nzd_currency)

    @api.multi
    def _count_quotation(self):
        for so in self:
            so.quote_count = self.search_count([('partner_id', '=', so.partner_id.id), ('state', '=', 'draft')])

    @api.multi
    @api.depends('ship_zip', 'warehouse_id', 'carrier_id', 'partner_id')
    def _is_rural_delivery(self):
        for order in self:
            if order.warehouse_id.id == 1 and order.carrier_id.name != 'Pickup':
                zip_code = order.ship_zip and order.ship_zip or order.partner_id.zip
                rural = self.env['nz.rural.delivery'].search([('name', '=', zip_code)])
                if rural:
                    order.is_rural_delivery = True

    @api.multi
    @api.depends('partner_id', 'partner_id.email')
    def _frequency(self):
        for order in self:
            freq = 'first'
            if order.partner_id.email:
                order_exists = self.search_count([('partner_id.email', '=', order.partner_id.email), ('id', '!=', order.id), ('date_order', '<=', order.date_order), ('state', '!=', 'cancel')])
                if order_exists:
                    freq = 'repeat'
            order.frequency = freq

    @api.multi
    @api.depends('order_line.tax_id')
    def _is_tax_missing(self):
        for order in self:
            order.is_tax_missing = bool([l for l in order.order_line if not l.tax_id])

    @api.multi
    @api.depends('order_line')
    def _order_weight(self):
        for order in self:
            order.order_weight = sum([l.product_id.weight for l in order.order_line if l.product_id and l.product_id.weight])

    @api.multi
    def _get_total_quantity(self):
        for so in self:
            so.total_qty = sum([l.product_uom_qty for l in so.order_line if l.product_id.type != 'service'])

    @api.multi
    def _fraud_score_details(self):
        for order in self:
            order.fraud_score_details = json.dumps(dict(
                [(str(p.amount), p.fraud_score_details) for p in order.sale_order_payment_id if p.fraud_score_details]))

    @api.multi
    def _display_address(self):
        for so in self:
            so.address_format = so.ship_country_id.address_format or \
                             "%(street)s\n%(street2)s\n%(city)s %(state_code)s %(zip)s\n%(country_name)s"

            args = {
                'state_code': so.ship_state_id.code or '',
                'state_name': so.ship_state_id.name or '',
                'country_code': so.ship_country_id.code or '',
                'country_name': so.ship_country_id.name or '',
                'street': so.ship_street or '',
                'street2': so.ship_street2 or '',
                'zip': so.ship_zip or '',
                'tt_company_name': so.ship_tt_company_name or '',
                'city': so.ship_city or ''
            }
            so.display_address = so.address_format % args

    def _search_open_cases(self, operator, value):
        if operator == '=' and value in [False, 0]:
            self._cr.execute("""SELECT distinct order_id FROM crm_helpdesk ch WHERE state in ('draft','open','pending') AND order_id is not null GROUP BY order_id having count(*) > 0""")
            return [('id', 'not in', [x[0] for x in self._cr.fetchall()])]
        else:
            self._cr.execute("""SELECT distinct order_id FROM crm_helpdesk ch WHERE state in ('draft','open','pending') AND order_id is not null GROUP BY order_id having count(*) %s %s""" % (operator, value))
            return [('id', 'in', [x[0] for x in self._cr.fetchall()])]

    def _search_product_cases(self, operator, value):
        if operator == '=' and value in [False, 0]:
            self._cr.execute("""
                SELECT
                    distinct order_id
                FROM
                    crm_helpdesk ch
                    LEFT JOIN crm_lead_tag clt on (clt.id = ch.categ_id)
                    LEFT JOIN rel_crm_helpdesk_resolution rchr on ( rchr.case_id = ch.id )
                    LEFT JOIN crm_helpdesk_resolution chr on (rchr.resolution_id = chr.id)
                WHERE
                    state != 'cancel' AND
                    (clt.product_case=True or chr.product_case=True) AND
                    order_id is not null
                GROUP BY
                    order_id
                having
                    count(*) > 0""")
            return [('id', 'not in', [x[0] for x in self._cr.fetchall()])]
        else:
            self._cr.execute("""
                SELECT
                    distinct order_id
                FROM
                    crm_helpdesk ch
                    LEFT JOIN crm_lead_tag clt on (clt.id = ch.categ_id)
                    LEFT JOIN rel_crm_helpdesk_resolution rchr on ( rchr.case_id = ch.id )
                    LEFT JOIN crm_helpdesk_resolution chr on (rchr.resolution_id = chr.id)
                WHERE
                    state != 'cancel' AND
                    (clt.product_case=True or chr.product_case=True)
                GROUP BY
                    order_id
                having count(*) %s %s""" % (operator, value))
            return [('id', 'in', [x[0] for x in self._cr.fetchall()])]

        return []

    def _search_delivery_order_status(self, operator, value):
        if value == 'none':
            self._cr.execute("SELECT distinct s.id FROM sale_order s LEFT JOIN stock_picking p ON s.id = p.sale_id WHERE p.sale_id is null")
        else:
            if type(value) != list:
                value = [value]
            self._cr.execute("""WITH dos as ( SELECT distinct on (s.id) s.id, p.name, pt.code as code, p.state, p.create_date from sale_order s, stock_picking p, stock_picking_type pt
                            WHERE p.sale_id = s.id AND p.picking_type_id = pt.id order by s.id, p.create_date desc) select id from dos WHERE code='outgoing' AND state in (%s);""" % (",".join(map(lambda x: "'" + x + "'", value))))
        return [('id', 'in', [x[0] for x in self._cr.fetchall()])]

    @api.multi
    def _search_future_order(self, operator, value):

        today = common.convert_tz(datetime.today(), self.env.user.tz)
        self._cr.execute("SELECT id from sale_order WHERE future_date>'%s' and order_status in ('future_delivery','future_payment')" % today.strftime('%Y-%m-%d'))

        if operator == '=' and value:
            return [('id', 'in', [x[0] for x in self._cr.fetchall()])]
        elif operator == '!=' and value:
            return [('id', 'not in', [x[0] for x in self._cr.fetchall()])]


    # ONCHANGE
    @api.onchange('ship_tt_company_name', 'ship_street', 'ship_street2', 'ship_city', 'ship_zip', 'ship_state_id', 'ship_country_id')
    def onchange_address_fields(self):

        val = {'change_in_address': True}
        domain = {}

        if self.ship_state_id:
            val['ship_country_id'] = self.ship_state_id.country_id.id

        if self.ship_country_id and not self.warehouse_id:
            if self.ship_country_id.code == 'NZ':
                val['warehouse_id'] = 1
            elif self.ship_country_id.code == 'AU':
                val['warehouse_id'] = 4

            domain['ship_state_id'] = [('country_id', '=', self.ship_country_id.id)]

        self.update(val)

        return {'domain': domain}

    @api.onchange('order_held', 'order_status')
    def onchange_order_status(self):
        vals = {}
        if self.order_status not in ['future_delivery', 'future_payment']:
            vals['future_date'] = False
        if not self.order_held:
            vals['future_date'] = False
            vals['order_status'] = False
        self.update(vals)

    @api.onchange('warehouse_id')
    def onchange_shop_id(self):
        if self.warehouse_id:
            vals = {
                'pricelist_id': self.env['product.pricelist'].search([('currency_id', '=', self.warehouse_id.company_id.currency_id.id)])
            }
            if not self.ship_country_id:
                vals['ship_country_id'] = self.warehouse_id.company_id.country_id.id
            self.update(vals)

    @api.multi
    @api.onchange('partner_id')
    def onchange_partner_id(self):
        if not self.partner_id:
            self.update({
                'partner_invoice_id': False,
                'partner_shipping_id': False,
                'payment_term_id': False,
                'fiscal_position_id': False,
                'last_ship_address': False,
            })
            return

        addr = self.partner_id.address_get(['delivery', 'invoice'])
        values = {
            'pricelist_id': self.partner_id.property_product_pricelist and self.partner_id.property_product_pricelist.id or False,
            'payment_term_id': self.partner_id.property_payment_term_id and self.partner_id.property_payment_term_id.id or False,
            'partner_invoice_id': addr['invoice'],
            'partner_shipping_id': addr['delivery'],
            'note': self.with_context(lang=self.partner_id.lang).env.user.company_id.sale_note,
            'last_ship_address': False,
        }

        if self.partner_id.phone:
            values['phone'] = self.partner_id.phone

            if self.last_partner_id.id != self.partner_id.id:
                values['last_partner_id'] = self.partner_id.id
                last_orders = self.search([('partner_id', '=', self.partner_id.id), ('ship_street', '!=', False),
                                           ('state', 'not in', ['cancel', 'draft'])], order='date_order desc',
                                          limit=1)
                if last_orders or (
                            self.partner_id.street or self.partner_id.street2 or self.partner_id.city or self.partner_id.zip):
                    values['last_ship_address'] = True

        self.update(values)


    # ORM
    @api.model
    def default_get(self, fields):
        resp = super(sale_order, self).default_get(fields)
        resp['picking_policy'] = 'one'
        # resp['order_policy'] = 'picking'
        if self._uid in [17, 18]:
            resp['carrier_id'] = 1
        if self.env.user.company_id.name == 'Group':
            resp['warehouse_id'] = False
        return resp

    @api.model
    def create(self, vals):
        vals = common.strip_sale_address(vals)
        if not vals.get('guid'):
            vals['guid'] = uuid.uuid4()
        return super(sale_order, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = common.strip_sale_address(vals)

        resp = super(sale_order, self).write(vals)

        flds = ['ship_tt_company_name', 'ship_street', 'ship_street2', 'ship_zip', 'ship_city', 'ship_state_id']
        if [fld for fld in flds if fld in vals]:
            order = self[0]
            depot_locs = 0
            if order.ship_tt_company_name:
                depot_locs = self.env['res.partner'].search_count([('is_depot', '=', True), ('tt_company_name', '=', order.ship_tt_company_name)])

            if not depot_locs:
                order.partner_id.update({
                    'tt_company_name': order.ship_tt_company_name,
                    'street': order.ship_street,
                    'street2': order.ship_street2,
                    'zip': order.ship_zip,
                    'city': order.ship_city,
                    'state_id': order.ship_state_id and order.ship_state_id.id or False,
                    'country_id': order.ship_country_id and order.ship_country_id.id or False,
                })
        return resp

    @api.multi
    def copy(self, default=None):

        default = default or {}
        if not self._context.get('skip_group_check'):
            groups = [(g.category_id and g.category_id.name or '') + "/" + g.name for g in self.env.user.groups_id]
            if 'Sales/Manager' not in groups:
                raise UserError('Only "Sales Manager" can duplicate Sales Order')

        sobj = self[0]
        if sobj.state not in ['sale', 'done', 'cancel']:
            raise UserError("Duplicate is allowed only in this status: 'Processing' or 'Done' or 'Cancel'")

        old_state = sobj.state

        default.update({
            'state': 'draft',
            'invoice_ids': [],
            'date_confirm': False,
        })

        vals = super(sale_order, self).copy_data(default)[0]

        new_vals = self.default_get(['name', 'date_order'])
        new_vals['state'] = 'draft'
        new_vals['order_line'] = []
        new_vals['partner_id'] = vals['partner_id']
        new_vals['warehouse_id'] = vals['warehouse_id']

        if old_state != 'cancel' and vals.get('date_order'):
            new_vals['date_order'] = vals.get('date_order')

        new_vals['user_id'] = vals['user_id']

        for line in vals['order_line']:
            line_vals = {
                'product_id': line[2]['product_id'],
                'product_uom_qty': line[2]['product_uom_qty'],
                'product_uom': line[2]['product_uom'],
                'price_unit': line[2]['price_unit'],
                'name': line[2]['name'],
                'tax_id': line[2]['tax_id']
            }
            new_vals['order_line'].append([0, 0, line_vals])
        new_order = self.create(new_vals)
        for line in new_order.order_line:
            line.product_id_change()

        return new_order


    # Actions/Links
    @api.multi
    def action_button_quote_revert(self):
        self.state = 'draft'
        return True

    @api.multi
    def add_payment(self):
        ctx = {'sale_order_id': self[0].id}
        if self[0].balance_remaining <= 0:
            ctx['default_type'] = 'refund'
            ctx['default_amount'] = 0

        return {
            'name': 'Payment',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'sale.order.payment',
            'target': 'new',
            'context': ctx
        }

    @api.multi
    def action_quote(self):
        self.write({'state': 'quote'})

    @api.multi
    def action_quote_revert(self):
        self.write({'state': 'draft'})

    @api.multi
    def clear_exceptions(self):
        return self.write({'dispatch_exception': False, 'exception_reason_ids': [[6, 0, []]]})

    @api.multi
    def unhold_order(self):
        return self.write({'order_held': False, 'order_status': False, 'future_date': False})

    @api.multi
    def action_cancel(self):
        ret_val = super(sale_order, self).action_cancel()
        for so in self:
            self.env['mail.message'].create({
                'body': u'<p>Sale Order cancelled by %s %s' % (self.env.user.name, common.convert_tz(datetime.today(), self.env.user.tz).strftime('%d/%m/%Y %I:%M %p %Z')),
                'model': 'sale.order',
                'res_id': so.id,
                'subtype_id': False,
                'author_id': self.env.user.partner_id.id,
                'type': 'comment'
            })
        return ret_val

    @api.multi
    def action_quotation_send(self):
        resp = super(sale_order, self).action_quotation_send()
        if self[0].state == 'quote':
            template = self.env['ir.model.data'].xmlid_to_object('tradetested.email_template_edi_quote')
            resp['context']['default_template_id'] = template.id
        return resp

    @api.multi
    def action_confirm_validate(self):
        self.ensure_one()
        sobj = self[0]

        context = self._context.copy()
        context['default_order_id'] = sobj.id
        validate_action = {
            'type': 'ir.actions.act_window',
            'name': 'Process Order',
            'res_model': 'sale.order.validate',
            'view_type': 'form',
            'view_mode': 'form',
            'target': 'new',
            'context': context,
        }

        if not self._context.get('rural_validated') and sobj.is_rural_delivery:
            gross_weight = sum(
                [(line.product_id.weight * line.product_uom_qty) for line in sobj.order_line if line.product_id if
                 line.product_id])
            if gross_weight >= 30.0:
                msg = 'Warning: Address postcode is likely to be Rural Delivery.\nPlease check address in NZ Post website before confirming'
                validate_action['context'].update(
                    {'default_msg': msg, 'default_validation': 'rural', 'default_partner_id': sobj.partner_id.id,
                     'default_rural_address': sobj.display_address})
                return validate_action

        if not self._context.get('general_validated'):
            total_cases = [c.id for c in sobj.case_ids if c.state in ['draft', 'open']]
            total_orders = [r.id for r in sobj.related_orders if r.state == 'draft']
            total_zendesk = [x.id for x in sobj.ticket_ids if x.state in ['New', 'Open', 'Pending']]

            msgs = []
            if total_cases or total_orders or total_zendesk:
                msg = ''
                msg += total_orders and (
                str(len(total_orders)) + " draft sale order" + (len(total_orders) > 1 and "s" or "")) or ""
                msg += total_cases and ((total_orders and " and " or "") + str(len(total_cases)) + " open case" + (
                len(total_orders) > 1 and "s" or "")) or ""
                msg += total_zendesk and (
                (total_orders + total_cases and " and " or "") + str(len(total_cases)) + " open Zendesk ticket" + (
                len(total_orders) > 1 and "s" or "")) or ""
                msg += " relating to this order.\n\n"
                msgs.append(msg)

            # stock
            msg = ''
            for line in sobj.order_line:
                if line.product_id.type == 'product' and line.product_uom_qty > line.product_id.qty_available:
                    msg += "SKU: %s\nQuantity on hand: %s" % (
                    line.product_id.default_code, line.product_id.qty_available)
                    if line.product_id.po_expected_date:
                        msg += "\nETA: " + datetime.strptime(line.product_id.po_expected_date, '%Y-%m-%d').strftime(
                            '%d %b %Y')
                    msg += "\n\n"
            if msg:
                msg = "Low Stock\n" + msg
                msgs.append(msg)

            # Company mismatch
            if any([line.id for line in sobj.order_line if
                    line.product_id.company_id.id != sobj.warehouse_id.company_id.id]):
                msg = "Sales order's and line item's country does not match."
                msgs.append(msg)

            # if Unreviewed Payments
            unreviewed_payments = ["$ %.2f by %s on %s" % (p.amount, common.PAYMENT_METHODS_DICT[p.method],
                                                           datetime.strptime(p.date, '%Y-%m-%d %H:%M:%S').strftime(
                                                               '%d %b')) for p in sobj.sale_order_payment_id if
                                   p.type == 'payment' and p.method == 'credit_card' and not p.reviewed]
            if unreviewed_payments:
                msg = "Warning: Order has unreviewed payments\n"
                msg += "\n".join(unreviewed_payments)
                msgs.append(msg)

            if msgs:
                context = self._context.copy()
                validate_action['context'].update({'default_msg': "\n\n".join(msgs), 'default_validation': 'general'})
                return validate_action

        if not self._context.get('tax_validated') and sobj.warehouse_id.default_tax_id:
            if [l.id for l in sobj.order_line if [t.id for t in l.tax_id] != [sobj.warehouse_id.default_tax_id.id]]:
                msg = "Tax does not match the default for '%s', Correct all lines?\n\n\nTip: Only select No if this order is for duty free export." % sobj.warehouse_id.name
                validate_action['context'].update(
                    {'default_msg': msg, 'default_order_id': sobj.id, 'default_validation': 'tax'})
                return validate_action

        # Verification
        if not self._context.get(
                'identity_validated') and sobj.warehouse_id.name == 'New Zealand' and sobj.carrier_id.name == 'Pickup':
            if [p.method for p in sobj.sale_order_payment_id if
                p.type == 'payment' and p.method not in ['eftpos', 'cash']]:
                validate_action['context'].update({'default_msg': '', 'default_validation': 'identity'})
                return validate_action

        resp = self.action_confirm()

        # Show DO for 'Sales Department'
        if self.env.user.team_id and ('Sales Department' in self.env.user.team_id.complete_name):
            return sobj.action_view_delivery()

        return resp

    # Functions
    @api.multi
    def delivery_set(self):

        # Remove delivery products from the sale order
        self._delivery_unset()

        for order in self:
            carrier = order.carrier_id
            if carrier:
                if order.state not in ('draft', 'sent'):
                    raise UserError(_('The order state have to be draft to add delivery lines.'))

                if carrier.delivery_type not in ['fixed', 'base_on_rule']:
                    # Shipping providers are used when delivery_type is other than 'fixed' or 'base_on_rule'
                    price_unit = order.carrier_id.get_shipping_price_from_so(order)[0]
                else:
                    # Classic grid-based carriers
                    carrier = order.carrier_id.verify_carrier(order.partner_shipping_id)
                    if not carrier:
                        raise UserError(_('No carrier matching.'))
                    price_unit = carrier.get_price_available(order)
                    if order.company_id.currency_id.id != order.pricelist_id.currency_id.id:
                        price_unit = order.company_id.currency_id.with_context(date=order.date_order).compute(
                            price_unit, order.pricelist_id.currency_id)

                order._create_delivery_line(carrier, price_unit)

            else:
                raise UserError(_('No carrier set for this order.'))

        return True

    @api.multi
    def update_draft_order_address(self):
        order = self[0]

        for draft_order in order.partner_id.sale_order_ids:
            if draft_order.state in ['draft']:
                draft_order.update({
                    'ship_tt_company_name': order.ship_tt_company_name,
                    'ship_street': order.ship_street,
                    'ship_street2': order.ship_street2,
                    'ship_zip': order.ship_zip,
                    'ship_city': order.ship_city,
                    'ship_state_id': order.ship_state_id and order.ship_state_id.id or False,
                    'ship_country_id': order.ship_country_id and order.ship_country_id.id or False,
                })

        order.partner_id.update({
            'tt_company_name': order.ship_tt_company_name,
            'street': order.ship_street,
            'street2': order.ship_street2,
            'zip': order.ship_zip,
            'city': order.ship_city,
            'state_id': order.ship_state_id and order.ship_state_id.id or False,
            'country_id': order.ship_country_id and order.ship_country_id.id or False,
        })
        order.update({'change_in_address': False})

    @api.multi
    def no_update_draft_order_address(self):
        order = self[0]
        order.partner_id.update({
            'tt_company_name': order.ship_tt_company_name,
            'street': order.ship_street,
            'street2': order.ship_street2,
            'zip': order.ship_zip,
            'city': order.ship_city,
            'state_id': order.ship_state_id and order.ship_state_id.id or False,
            'country_id': order.ship_country_id and order.ship_country_id.id or False,
        })
        order.update({'change_in_address': False})

    @api.multi
    def export_to_mailchimp(self):
        mcs = self.env['mailchimp.config'].search([('state', '=', 'Connected')])
        if mcs:
            for order in self:
                mcs[0].export_member(order.partner_id.id, order.company_id.id)
        return True

    @api.multi
    def use_last_ship_address(self):
        order = self[0]
        if order.partner_id.street:
            order.update({
                'ship_tt_company_name': order.partner_id.tt_company_name,
                'ship_street': order.partner_id.street,
                'ship_street2': order.partner_id.street2,
                'ship_zip': order.partner_id.zip,
                'ship_city': order.partner_id.city,
                'ship_state_id': order.partner_id.state_id and order.partner_id.state_id.id or False,
                'ship_country_id': order.partner_id.country_id and order.partner_id.country_id.id or False,
                'last_ship_address': False
            })
        else:
            last_orders = self.search([('partner_id', '=', order.partner_id.id), ('ship_street', '!=', False), ('state', 'not in', ['cancel', 'draft'])], order='date_order desc', limit=1)
            if last_orders:
                last_order = last_orders[0]
                order.update({
                    'ship_tt_company_name': last_order.ship_tt_company_name,
                    'ship_street': last_order.ship_street,
                    'ship_street2': last_order.ship_street2,
                    'ship_zip': last_order.ship_zip,
                    'ship_city': last_order.ship_city,
                    'ship_state_id': last_order.ship_state_id and last_order.ship_state_id.id or False,
                    'ship_country_id': last_order.ship_country_id and last_order.ship_country_id.id or False,
                    'last_ship_address': False
                })
        return True

    @api.multi
    def no_last_ship_address(self):
        for order in self:
            order.update({'last_ship_address': False})
        return True

    @api.multi
    def create_case_auto(self):
        order = self[0]
        vals = {
            'order_id': order.id,
            'name': order.name + ' - ' + self._context.get('case_name', ''),
            'user_id': self._context.get('user_id', order.user_id.id),
            'owner_id': self._context.get('owner_id', order.user_id.id),
            'team_id': self._context.get('team_id', order.team_id.id),
            'categ_id': self._context.get('categ_id', False),
            'resolution_ids': self._context.get('resolution_id', False) and [[6, 0, [self._context['resolution_id']]]] or False,
            'description': self._context.get('description', ''),
            'date_deadline': time.strftime('%Y-%m-%d')
        }
        if self._context.get('days_deadline', 0) > 0:
            vals['date_deadline'] = (datetime.today() + relativedelta(days=self._context['days_deadline'])).strftime('%Y-%m-%d')
        return self.env['crm.helpdesk'].create(vals)

    @api.multi
    def get_exceptions(self):
        exception_ids = self.env['sale.order.exception'].search([])
        for excpt in self.env['sale.order.exception'].browse(exception_ids):
            excpt.name = excpt.id

    @api.multi
    def trademe_feedback(self, message="", type=1):
        session = requests_oauthlib.OAuth1Session(
            "FDDE0F841169C7C867D23E42E1AE0E37F7",
            client_secret="9A22AFF4090006DFFC0EA34C20F055F72A",
            resource_owner_key="E337AFE7F762479B6EBC635331ECFAA493",
            resource_owner_secret="83550E721D8D88A4149B9D4772189E8953"
        )

        for order in self:
            if not order.trademe_purchase_id or order.trademe_purchase_id == 0:
                continue

            feedback = {
                "FeedbackType": type,
                "Text": message,
                "PurchaseId": str(order.trademe_purchase_id)
            }
            resp = session.post('https://api.trademe.co.nz/v1/MyTradeMe/Feedback.json', data=json.dumps(feedback), headers={'Content-Type': 'application/json'})
            if resp.status_code != 200:
                _logger.error(resp.text)
        return True

    @api.multi
    def check_outofstock_incoming(self):
        order = self[0]
        for line in order.order_line:
            if line.product_id.qty_available <= 0 and line.product_id.incoming_qty > 0:
                return True
        return False

    @api.multi
    @api.returns('self', lambda value: value.id)
    def message_post(self, body='', subject=None, message_type='notification', subtype=None, parent_id=False, attachments=None, content_subtype='html', **kwargs):
        if self.partner_id.id not in [f.id for f in self.message_follower_ids]:
            self.message_subscribe([self.partner_id.id], force=False)
        return super(sale_order, self).message_post(body, subject, message_type, subtype, parent_id, attachments, content_subtype, **kwargs)

    # Crons
    @api.model
    def cash_up_cron(self, ids=None):

        today = common.convert_tz(datetime.today(), 'Pacific/Auckland').replace(tzinfo=None)
        midnight_utc = common.convert_to_utc(
            datetime.strptime(today.strftime('%Y-%m-%d 00:00:01'), '%Y-%m-%d %H:%M:%S'),
            'Pacific/Auckland').replace(tzinfo=None)

        self._cr.execute(
            "select sum(amount) from sale_order_payment WHERE method = 'cash' and type='payment' AND date >= '%s';" % midnight_utc.strftime(
                '%Y-%m-%d %H:%M:%S'))
        payment_amount = self._cr.fetchone()[0]
        if not payment_amount:
            payment_amount = 0

        self._cr.execute(
            "select sum(amount) from sale_order_payment WHERE method = 'cash' and type='refund' AND date >= '%s';" % midnight_utc.strftime(
                '%Y-%m-%d %H:%M:%S'))
        refund_amount = self._cr.fetchone()[0]
        if not refund_amount:
            refund_amount = 0

        showroom_cash = payment_amount - refund_amount

        if showroom_cash != 0:
            body = '''
                    <strong>Date: %s</strong><br/>
                    <strong>Today's cash sales: %.2f</strong><br/>
                    <strong>Cash to safe:</strong> ____<br/>
                    <strong>Float balance:</strong> ____<br/>
                    <strong>Reason for any variance:</strong><br/>

                    <br/><br/>
                    <p>Place cash into sealable envelope and write the date and amount on the outside of envelope.

                    <p>Complete above and reply to this email (accounts@tradetested.co.nz).
                ''' % (today.strftime('%d/%m/%Y'), showroom_cash)

            vals = {
                'email_from': 'accounts@tradetested.co.nz',
                'reply_to': 'accounts@tradetested.co.nz',
                'email_to': 'showroomteam@tradetested.co.nz,accounts@tradetested.co.nz',
                'state': 'outgoing',
                'subject': 'Showroom cash up %s' % today.strftime('%d/%m/%Y'),
                'body_html': body,
                'auto_delete': False,
            }

            self.env['mail.mail'].create(vals)

    @api.multi
    def confirm_sale_auto(self):

        excpt_map = self.get_exceptions()

        for so in self:

            if so.dispatch_exception:
                continue

            so_exceptions = []

            if so.user_id.id != 1:
                so_exceptions.append(excpt_map['Full Order Check'])
            else:
                oos = [line.stock_indicator for line in so.order_line if line.stock_indicator != 'g']
                if so.warehouse_id.name == 'Australia' and so.carrier_id.name == 'Pickup' and oos:
                    so_exceptions.append(excpt_map['Australian Pickup OOS'])

                if so.channel == 'trademe' and oos:
                    so_exceptions.append(excpt_map['Trade Me OOS'])

                if so.channel == 'ebay' and oos:
                    so_exceptions.append(excpt_map['eBay OOS'])

                if not so.phone and sum([l.product_id.weight for l in so.order_line if l.product_id]) > 10:
                    so_exceptions.append(excpt_map['Phone Number Required'])

                if not so.ship_street or not so.ship_city:
                    so_exceptions.append(excpt_map['Shipping Address Required'])

                if (so.ship_street and ((not bool(re.search(r'\d', so.ship_street)) or len(so.ship_street) > 25))) or not so.ship_country_id or not so.ship_zip:
                    so_exceptions.append(excpt_map['Shipping Address Check'])

                if so.delivery_instructions and 'Depot' not in so.ship_tt_company_name:
                    so_exceptions.append(excpt_map['Delivery Instructions Check'])

                if any([line.id for line in so.order_line if not line.product_id]):
                    so_exceptions.append(excpt_map['Product SKU Check'])

                if any([line.id for line in so.order_line if 'Delivery to' in line.name]) and 'Depot' not in (so.ship_tt_company_name or ''):
                    so_exceptions.append(excpt_map['Depot Delivery Required'])

                if so.carrier_id.name != 'Pickup' and so.is_rural_delivery and (any([line.id for line in so.order_line if line.product_id.shipping_group]) or sum([l.product_id.weight for l in so.order_line if l.product_id]) > 25):
                    so_exceptions.append(excpt_map['Freight Quote Required'])
                elif any([line.id for line in so.order_line if 'Standard Delivery' in line.name]):
                    so_exceptions.append(excpt_map['Freight Quote Required'])

                if max([0] + [p.fraud_score_details.get('risk_score', 0) for p in so.sale_order_payment_id if p.fraud_score_details]) > 1:
                    so_exceptions.append(excpt_map['Fraud Score Check'])

                if any([p.id for p in so.sale_order_payment_id if p.type == 'payment' and not p.reviewed]):
                    so_exceptions.append(excpt_map['Payment Review Required'])

                if any([c.id for c in so.case_ids if c.state in ['draft', 'open', 'pending']]):
                    so_exceptions.append(excpt_map['Open Case Check'])

                if any([z.id for z in so.ticket_ids if z.state in ['New', 'Open', 'Pending']]):
                    so_exceptions.append(excpt_map['Open Ticket Check'])

                if any([ro.id for ro in so.related_orders if ro.state == 'draft']):
                    so_exceptions.append(excpt_map['Related Order Check'])

            vals = {'dispatch_exception': bool(so_exceptions), 'exception_reason_ids': [[6, 0, so_exceptions]]}

            if excpt_map['Phone Number Required'] in so_exceptions:
                vals['order_held'] = True
                template_ids = self.env['email.template'].search([('name', '=', 'Phone number request')])
                if not template_ids:
                    _logger.error('Email Template "Phone number request" not found')
                else:
                    self.env['email.template'].send_mail(template_ids[0], so.id)

            if excpt_map['Shipping Address Required'] in so_exceptions:
                vals['order_held'] = True
                template_ids = self.env['email.template'].search([('name', '=', 'Address request')])
                if not template_ids:
                    _logger.error('Email Template "Phone number request" not found')
                else:
                    self.env['email.template'].send_mail(template_ids[0], so.id)

            if so.state == 'shipping_except':
                vals['order_held'] = True

            so.write(vals)

            if not so_exceptions:
                self._context = {'skip_message': True, 'rural_delivery_confirm': True}
                self.action_confirm()

        return True


class sale_order_payment(models.Model):
    _name = 'sale.order.payment'
    _description = 'Sale Order Payment'
    _order = 'date desc'

    @api.multi
    def _default_is_cc_visible(self):
        user_groups = [g.name for g in self.env.user.groups_id]
        if ('Shop Salesperson' in user_groups) and ('Dispatch Coordinator' not in user_groups):
            return False
        return True

    @api.multi
    def _is_cc_visible(self):
        user_groups = [g.name for g in self.env.user.groups_id]
        for payment in self:
            if ('Shop Salesperson' in user_groups) and ('Dispatch Coordinator' not in user_groups):
                payment.is_cc_visible = False
            else:
                payment.is_cc_visible = True

    @tools.ormcache()
    def _selection_method(self):
        return [(m['code'], m['name']) for m in self.env['sale.order.payment.method'].search([])]

    @api.multi
    def _get_refund_max(self, order):
        refund_max = 0
        for payment in order.sale_order_payment_id:
            if payment.method == 'credit_card_auto':
                if payment.type == 'payment':
                    refund_max += payment.amount
                elif payment.type == 'refund':
                    refund_max -= payment.amount
        return refund_max

    type = fields.Selection((('payment', 'Payment'), ('refund', 'Refund')), 'Type', default='payment')
    amount = fields.Float('Amount', digits=(11, 2))
    comment = fields.Char('Comment', size=2048)
    sale_order_id = fields.Many2one('sale.order', 'Sale Order Reference', required=True, ondelete='cascade', index=True, readonly=False)
    sale_return_id = fields.Many2one('sale.order.return', 'Return Order', ondelete='restrict', index=True, readonly=False)
    method = fields.Selection(selection=common.payment_methods, string='Method', required=True)
    date = fields.Datetime('Created', default=lambda self: time.strftime('%Y-%m-%d %H:%M:%S'))
    user_id = fields.Many2one('res.users', 'Created By', default=lambda self: self.env.user)
    reviewed = fields.Boolean('Reviewed', default=lambda self: self.env.user.id == 1)
    reviewed_date = fields.Date('Reviewed Date')
    reviewer_id = fields.Many2one('res.users', 'Reviewed by')
    is_cc_visible = fields.Boolean(compute='_is_cc_visible', string='CC Visible', default=_default_is_cc_visible)
    cc_holder = fields.Char(string='Name on Card', size=256)
    cc_number = fields.Char(string='Card Number', size=256)
    cc_cvc = fields.Char(string='CVC', size=256)
    cc_expiry = fields.Char(string='Expiry', size=4)
    cc_txn_ref = fields.Char('Txn Ref', size=16)
    dpstxnref = fields.Char('Dps Txn Ref', size=64, readonly=True)
    cc_status = fields.Char(string="CC Validated", size=16)
    cc_last4 = fields.Char('CC Last 4')
    cc_exp_dt = fields.Char('CC Expiry MMYY')
    fraud_score_details = fields.Serialized('Fraud Score Details')
    transfer_from_id = fields.Many2one('sale.order', 'Transfer From')
    transfer_to_id = fields.Many2one('sale.order', 'Transfer To')
    date_rcpt_sent = fields.Datetime('Receipt Sent Date')
    refund_max = fields.Float('Max', help="Max Refund")

    # ORM
    @api.model
    def default_get(self, fields):
        resp = super(sale_order_payment, self).default_get(fields)
        if 'sale_order_id' in self._context:
            order = self.env['sale.order'].browse(self._context['sale_order_id'])
            bal_remaining = order.balance_remaining
            if bal_remaining > 0:
                resp['amount'] = bal_remaining
            resp['sale_order_id'] = self._context['sale_order_id']
            resp['refund_max'] = self._get_refund_max(order)

        if 'sale_return_id' in self._context:
            bal_remaining = self.env['sale.order.return'].browse(self._context['sale_return_id']).balance_remaining
            if bal_remaining > 0:
                resp['amount'] = bal_remaining
            resp['sale_return_id'] = self._context['sale_return_id']
        return resp

    @api.model
    def create(self, vals):

        if vals.get('type') == 'payment' and vals.get('method') == 'transfer' and vals.get('transfer_from_id') and vals.get('amount', 0) > 0:
            transfer_from = self.env['sale.order'].browse(vals['transfer_from_id'])
            if vals['amount'] > transfer_from.payments_total_less_refunds:
                raise UserError('Insufficient Balance, Payment balance on Order %s is %s' % (transfer_from.name, transfer_from.payments_total_less_refunds))

        elif vals.get('type') == 'refund' and vals.get('method') == 'transfer' and vals.get('transfer_to_id') and vals.get('amount', 0) > 0:
            transfer_from = self.env['sale.order'].browse(vals['sale_order_id'])
            if vals['amount'] > transfer_from.payments_total_less_refunds:
                raise UserError('Insufficient Balance, Payment balance on Order %s is %s' % (transfer_from.name, transfer_from.payments_total_less_refunds))

        elif vals.get('type') == 'refund' and vals.get('method') == 'credit_card_auto':
            order = self.pool.get('sale.order').browse(vals['sale_order_id'])
            refund_max = self._get_refund_max(order)
            if vals['amount'] > refund_max:
                raise UserError('Can not process', 'Refund amount is more than maximum allowed for this order')

        resp = super(sale_order_payment, self).create(vals)
        order = self.env['sale.order'].browse(vals['sale_order_id'])

        if (vals['method'] == 'credit_card_auto') and (not vals.get('cc_txn_ref')):
            if vals['type'] == 'payment':
                if not (vals.get('cc_holder') and vals.get('cc_number') and vals.get('cc_cvc') and vals.get('cc_expiry')):
                    raise UserError('CC Fields', 'CC Fields are required to process Payment')

                data = {
                    'order_id': order.id,
                    'txn_ref': self.env['ir.sequence'].next_by_code('dps.transaction'),
                    'payment_id': resp.id,
                    'order_number': order.name,
                    'cc_holder': vals['cc_holder'],
                    'cc_number': vals['cc_number'],
                    'cc_cvc': vals['cc_cvc'],
                    'cc_expiry': vals['cc_expiry'],
                    'cc_capture_amount': vals['amount'],
                    'company_id': order.company_id.id
                }

                txn_resp = self.env['cc.payment.express'].transaction_purchase(data)

                resp.update({
                    'reviewed': True,
                    'reviewer_id': 1,
                    'cc_txn_ref': data['txn_ref'],
                    'dpstxnref': txn_resp[1],
                    'comment': 'CC Number: ' + txn_resp[0] + ', Transaction: ' + data['txn_ref'] + ', DPS Ref:  ' + txn_resp[1],
                    'cc_last4': vals['cc_number'][-4:],
                    'cc_exp_dt': vals['cc_expiry']
                })

            if vals['type'] == 'refund':
                data = {
                    'order_id': order.id,
                    'payment_id': resp,
                    'order_number': order.name,
                    'cc_refund_amount': vals['amount'],
                    'company_id': order.company_id.id
                }
                txn_resp = self.env['cc.payment.express'].transaction_refund(data)
                self.write({'reviewed': True, 'reviewer_id': 1, 'comment': txn_resp, 'dpstxnref': '_'})

        if vals['method'] == 'voucher' and vals['type'] == 'refund':
            msg = {
                "type": "credit",
                "customer_email": order.partner_id.email,
                "customer_name": order.partner_id.name,
                "odoo_order_id": order.id,
                "value": vals['amount'],
                "store_code": {'New Zealand': 'nz', 'Australia': 'au'}[order.warehouse_id.company_id.name]
            }
            if order.partner_id.magento_id:
                msg['magento_customer_id'] = order.partner_id.magento_id
            self.env['rabbitmq'].push([], queue='odoo.bus.vouchers', message=msg)

        if resp.sale_order_id.state == 'quote' and vals.get('type') == 'payment' and vals.get('amount', 0) > 0:
            resp.sale_order_id.update({'state': 'draft'})

        return resp

    @api.model
    def fields_get(self, fields=None):
        res = super(sale_order_payment, self).fields_get(fields)
        if self.env.context.get('sale_order_id') and res.get('method'):
            if self.env.user.team_id.id:
                res['method']['selection'] = [(p.code, p.name) for p in self.env['sale.order.payment.method'].search(
                    [('team_ids', '=', self.env.user.team_id.id)])]
        return res

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        if self._context.get('filter_send_rcpt'):
            self._cr.execute("""SELECT
                                    p.id,
                                    so.id
                                from
                                    sale_order_payment p,
                                    sale_order so,
                                    sale_order_payment_method pm
                                where
                                    p.sale_order_id = so.id and
                                    p.method = pm.code and
                                    pm.send_rcpt_email = True and
                                    p.date_rcpt_sent is null and
                                    (p.date>so.date_payment_confirm or so.date_payment_confirm is null) and
                                    ( so.balance_remaining != 0 or p.type='refund')""")
            resp = [x[0] for x in self._cr.fetchall()]
            return resp
        return super(sale_order_payment, self).search(args, offset, limit, order, count)

    @api.onchange('cc_number')
    def onchange_card(self):
        if self.cc_number:
            if common.luhn(self.cc_number):
                self.cc_status = 'valid'
            else:
                self.cc_status = 'invalid'
        else:
            self.cc_status = ''

    @api.onchange('method', 'sale_order_id')
    def onchange_method(self):
        if self.method == 'transfer' and self.sale_order_id:
            domain = [('id', 'in', [r.id for r in self.sale_order_id.related_orders])]
            return {'domain': {
                'transfer_from_id': domain,
                'transfer_to_id': domain
            }}

    @api.multi
    def save(self):
        if self.type == 'payment' and self.method == 'transfer' and self.transfer_from_id and self.amount > 0:
            if self.amount > self.transfer_from_id.payments_total_less_refunds:
                raise UserError('Insufficient Balance, Payment balance on Order %s is %s' % (self.transfer_from_id.name, self.transfer_from_id.payments_total_less_refunds))
            self.create({
                'type': 'refund',
                'method': 'transfer',
                'amount': self.amount,
                'comment': 'To ' + self.sale_order_id.name,
                'sale_order_id': self.transfer_from_id.id
            })
            self.update({'comment': 'From ' + self.transfer_from_id.name + (self.comment and (', ' + self.comment) or '')})

        elif self.type == 'refund' and self.method == 'transfer' and self.transfer_to_id and self.amount > 0:
            if self.amount > self.sale_order_id.payments_total_less_refunds + self.amount:
                raise UserError('Insufficient Balance, Payment balance on Order %s is %s' % (self.sale_order_id.name, self.sale_order_id.payments_total_less_refunds + self.amount))
            self.create({
                'type': 'payment',
                'method': 'transfer',
                'amount': self.amount,
                'comment': 'From ' + self.sale_order_id.name,
                'sale_order_id': self.transfer_to_id.id
            })
            self.update({'comment': 'To ' + self.transfer_to_id.name + (self.comment and (', ' + self.comment) or '')})

        return True


class sale_order_payment_method(models.Model):
    _name = 'sale.order.payment.method'
    _order = 'sequence'

    sequence = fields.Integer('Sequence')
    code = fields.Char('Code', required=True)
    name = fields.Char('Name', required=True)
    active = fields.Boolean('Active', default=True)
    send_rcpt_email = fields.Boolean('Send Receipt Emails', default=True)
    team_ids = fields.Many2many('crm.team', 'sales_team_payments_method_rel', 'method_id', 'team_id', 'Sales Teams')

    _sql_constraints = [
        ('name_uniq', 'unique (name)', 'Payment Method must be unique!'),
    ]

    @api.multi
    @api.depends('parent_id', 'name', 'parent_id.name')
    def _complete_name(self):
        for team in self:
            name = team.name
            parent = team.parent_id
            while parent:
                name = parent.name + ' / ' + name
                parent = parent.parent_id
            team.complete_name = name


class sale_order_held_category(models.Model):
    _name = 'sale.order.held.category'

    code = fields.Char('Code', size=64, required=True)
    name = fields.Char('Held Category', size=255, required=True)


class crm_team(models.Model):
    _inherit = 'crm.team'

    payment_method_ids = fields.Many2many('sale.order.payment.method', 'sales_team_payments_method_rel', 'team_id', 'method_id', 'Payment Methods')


class sale_order_marketing_method(models.Model):
    _name = 'sale.order.marketing.method'

    sequence = fields.Integer('Sequence')
    code = fields.Char('Code', size=64)
    name = fields.Char('Name', size=128)
    active = fields.Boolean('Active', default=True)


class sale_channel(models.Model):
    _name = 'sale.channel'

    code = fields.Char('Code')
    name = fields.Char('Name')


class nz_rural_delivery(models.Model):
    _name = 'nz.rural.delivery'

    name = fields.Char('Post Code', size=64)


class sale_order_exception(models.Model):
    _name = 'sale.order.exception'

    sequence = fields.Integer('Sequence')
    name = fields.Char('Exception Reason', size=255, required=True)

    _sql_constraints = [('name_uniq', 'unique (name)', 'Exception reason must be unique!'), ]


class sale_period(models.Model):
    _name = 'sale.period'

    name = fields.Char('Name', required=True)
    period_start = fields.Date('Period Start', required=True)
    period_end = fields.Date('Period End', required=True)


class sale_period_target(models.Model):
    _name = 'sale.period.target'

    user_id = fields.Many2one('res.users', 'User', required=True)
    period_id = fields.Many2one('sale.period', 'Target Period', required=True)
    amount = fields.Float('Amount', required=True)

    _sql_constraints = [
        ('period_uniq', 'unique(period_id,user_id)', 'Multiple target for same salesperson and same period is not allowed'),
    ]
