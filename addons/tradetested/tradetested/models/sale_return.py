# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError
import odoo.addons.decimal_precision as dp
import common
import logging


_logger = logging.getLogger(__name__)



class sale_order_return(models.Model):
    _name = 'sale.order.return'
    _description = 'Return Order'
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    order_id = fields.Many2one('sale.order', 'Sale Order')
    name = fields.Char(related='order_id.name')
    partner_id = fields.Many2one(related='order_id.partner_id', string='Customer')
    pricelist_id = fields.Many2one(related='order_id.pricelist_id', string='Pricelist')
    currency_id = fields.Many2one(related='order_id.currency_id', store=True, string='Currency', readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', string='Company')
    carrier_id = fields.Many2one("delivery.carrier", "Delivery Method", readonly=True, states={'draft': [('readonly', False)]})

    return_lines = fields.One2many('sale.order.return.line', 'return_order_id', domain=[('line_type', '=', 'return')], string='Products To Return', readonly=True, states={'draft': [('readonly', False)]})
    return_amount_untaxed = fields.Float(compute='_return_amount_all', digits=dp.get_precision('Account'), string='Untaxed Amount', track_visibility='always', store=True)
    return_amount_tax = fields.Float(compute='_return_amount_all', digits=dp.get_precision('Account'), string='Taxes', store=True)
    return_amount_total = fields.Float(compute='_return_amount_all', digits=dp.get_precision('Account'), string='Total', store=True)
    send_lines = fields.One2many('sale.order.return.line', 'return_order_id', domain=[('line_type', '=', 'send')], string='Proudcts To Send', readonly=True, states={'draft': [('readonly', False)]})
    amount_untaxed = fields.Float(compute='_amount_all', digits=dp.get_precision('Account'), string='Untaxed Amount', track_visibility='always', store=True)
    amount_tax = fields.Float(compute='_amount_all', digits=dp.get_precision('Account'), string='Taxes', multi='sums', store=True)
    amount_total = fields.Float(compute='_amount_all', digits=dp.get_precision('Account'), string='Total', multi='sums', store=True)

    create_date = fields.Datetime('Date Created')
    state = fields.Selection([('draft', 'Draft'), ('processing', 'Processing'), ('done', 'Done'), ('cancel', 'Cancelled')], 'Status', default='draft')
    picking_ids = fields.One2many('stock.picking', 'sale_return_id', 'Related Picking')
    delivery_order_status = fields.Selection(compute='_delivery_order_status', selection=common.DELIVERY_STATES_RETURN, string="Delivery Order Status")
    reviewed = fields.Boolean('Reviewed')
    reviewed_date = fields.Date('Reviewed Date')
    reviewer_id = fields.Many2one('res.users', 'Reviewed By')
    balance_remaining = fields.Float(compute='_balance_remaining', string="Return Balance")
    so_balance_remaining = fields.Monetary(related='order_id.balance_remaining', string='Sales Order Balance')
    payment_ids = fields.One2many('sale.order.payment', 'sale_return_id', 'Payments')
    show_process_button = fields.Boolean(compute='_show_process_button', string="Process Button")
    type = fields.Selection(compute='_define_type', selection=[('return', 'Return'), ('exchange', 'Exchange'), ('replacement', 'Replacement')], store=True, string='Type')

    @api.multi
    def _show_process_button(self):
        disp_cord_grp_id = self.env['ir.model.data'].sudo().xmlid_to_res_id('tradetested.group_stock_dispatch')
        salesperson_grp_id = self.env['ir.model.data'].sudo().xmlid_to_res_id('tradetested.group_shop_salesperson')
        groups = self.env.user.groups_id.ids

        for reto in self:
            if reto.state not in ['cancel']:
                if (disp_cord_grp_id in groups and (reto.balance_remaining >= 0.00 and reto.balance_remaining < 0.01)) or \
                        (salesperson_grp_id in groups and reto.balance_remaining < 0.01):
                    self.show_process_button = True

    @api.multi
    def _balance_remaining(self):
        for reto in self:
            balance_remaining = reto.amount_total - reto.return_amount_total
            for payment in reto.payment_ids:
                if payment.type == 'payment':
                    balance_remaining -= payment.amount
                elif payment.type == 'refund':
                    balance_remaining += payment.amount
            self.balance_remaining = balance_remaining

    @api.depends('send_lines', 'return_lines')
    def _define_type(self):
        for reto in self:
            if reto.send_lines and reto.return_lines:
                self.type = 'exchange'
            elif reto.return_lines:
                self.type = 'return'
            elif reto.send_lines:
                self.type = 'replacement'

    @api.multi
    def _delivery_order_status(self):
        res = {}
        for reto in self:
            if reto.picking_ids:
                self.delivery_order_status = reto.picking_ids and reto.picking_ids[0].state or 'False'

    @api.depends('send_lines.price_total')
    def _amount_all(self):
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.send_lines:
                amount_untaxed += line.price_subtotal
                amount_tax += line.price_tax
            order.update({
                'amount_untaxed': order.pricelist_id.currency_id.round(amount_untaxed),
                'amount_tax': order.pricelist_id.currency_id.round(amount_tax),
                'amount_total': amount_untaxed + amount_tax,
            })

    @api.depends('return_lines.price_total')
    def _return_amount_all(self):
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.return_lines:
                amount_untaxed += line.price_subtotal
                amount_tax += line.price_tax
            order.update({
                'return_amount_untaxed': order.pricelist_id.currency_id.round(amount_untaxed),
                'return_amount_tax': order.pricelist_id.currency_id.round(amount_tax),
                'return_amount_total': amount_untaxed + amount_tax,
            })

    @api.model
    def default_get(self, fields):
        resp = super(sale_order_return, self).default_get(fields)
        order = False
        if self._context.get('active_model') == 'crm.helpdesk' and self._context.get('active_id'):
            case = self.env['crm.helpdesk'].browse(self._context['active_id'])
            if case.order_id:
                order = case.order_id

        if self._context.get('active_model') == 'sale.order' and self._context.get('active_id'):
            order = self.env['sale.order'].browse(self._context['active_id'])

        if order:
            resp['order_id'] = order.id
            resp['company_id'] = order.company_id.id
            order_products = []
            if order.carrier_id:
                resp['carrier_id'] = order.carrier_id.id
            for line in order.order_line:
                if line.product_id.type != 'service' and line.product_uom_qty > 0 and ((line.product_uom_qty - line.qty_returned) > 0.01):
                    if 'return_lines' not in resp:
                        resp['return_lines'] = []
                    if 'send_lines' not in resp:
                        resp['send_lines'] = []
                    resp['return_lines'].append([0, 0, {'product_id': line.product_id.id, 'name': line.name, 'line_type': 'return',
                                                        'tax_id': [[6, 0, [t.id for t in line.tax_id]]], 'company_id': order.company_id.id,
                                                        'sale_order_line_id': line.id,
                                                        'price_unit': line.price_unit, 'product_uom_qty': (line.product_uom_qty - line.qty_returned)}])
                    resp['send_lines'].append([0, 0, {'product_id': line.product_id.id, 'name': line.name, 'line_type': 'send',
                                                      'tax_id': [[6, 0, [t.id for t in line.tax_id]]], 'company_id': order.company_id.id,
                                                      'price_unit': line.price_unit, 'product_uom_qty': (line.product_uom_qty - line.qty_returned)}])
        return resp

    @api.multi
    def reset_return_lines(self):
        sobj = self[0]

        for line in sobj.return_lines:
            line.unlink()

        resp = self.with_context({'active_model': 'sale.order', 'active_id': sobj.order_id.id}).default_get(['return_lines'])
        for line_vals in resp.get('return_lines', []):
            line_vals[2]['return_order_id'] = sobj.id
            self.env['sale.order.return.line'].create(line_vals[2])
        return True

    @api.multi
    def reset_send_lines(self):

        sobj = self[0]
        for line in sobj.send_lines:
            line.unlink()

        resp = self.with_context({'active_model': 'sale.order', 'active_id': sobj.order_id.id}).default_get(['send_lines'])
        for line_vals in resp.get('send_lines', []):
            line_vals[2]['return_order_id'] = sobj.id
            self.env['sale.order.return.line'].create(line_vals[2])
        return True

    @api.multi
    def process_sale_return(self):

        sobj = self[0]

        if not sobj.return_lines and not sobj.send_lines:
            raise UserError('There should at least one line in return lines or send lines')

        if sobj.balance_remaining > 0:
            raise UserError('There is balance remaining, please record payment first')

        if sobj.return_lines:

            holding_loc = self.env['stock.location'].search([('name', '=', 'Return Processing')])
            if not holding_loc:
                raise UserError('Please Create Stock Location "Return Processing"')

            if sobj.company_id.name == 'New Zealand':
                location_id = 9
                location_dest_id = holding_loc[0].id
            elif sobj.company_id.name == 'Australia':
                location_id = 9
                location_dest_id = holding_loc[0].id
            else:
                raise UserError('Can not decide location')

            picking_vals = {
                'company_id': sobj.company_id.id,
                'picking_type_id': self.env['stock.picking.type'].search([('code', '=', 'incoming'), ('warehouse_id.company_id.id', '=', sobj.company_id.id)])[0].id,
                'move_lines': [],
                'move_type': 'one',
                'sale_id': sobj.order_id.id,
                'sale_return_id': sobj.id,
                'partner_id': sobj.partner_id.id,
                'origin': sobj.order_id.name
            }

            for line in sobj.return_lines:

                if line.product_uom_qty <= 0:
                    raise UserError('Return lines quantity required')

                picking_vals['move_lines'].append([0, False, {
                    'company_id': sobj.company_id.id,
                    'location_dest_id': location_dest_id,
                    'location_id': location_id,
                    'product_id': line.product_id.id,
                    'name': line.product_id.name,
                    'product_uom_qty': line.product_uom_qty,
                    'product_uom': line.product_id.uom_id.id,
                }])
                if line.price_unit > 0.01:
                    self.env['sale.order.line'].create({
                        'order_id': sobj.order_id.id,
                        'product_id': line.product_id.id,
                        'name': 'RETURN ' + line.product_id.name,
                        'product_uom_qty': line.product_uom_qty * -1,
                        'price_unit': line.price_unit,
                        'tax_id': [[6, 0, [t.id for t in line.tax_id]]]
                    })
                line.sale_order_line_id.write({'qty_returned': line.product_uom_qty})
            ret_picking_id = self.env['stock.picking'].create(picking_vals)

        if sobj.send_lines:

            if sobj.company_id.name == 'New Zealand':
                location_id = 12
                location_dest_id = 9
            elif sobj.company_id.name == 'Australia':
                location_id = 15
                location_dest_id = 9
            else:
                raise UserError('Can not decide location')

            picking_vals = {
                'company_id': sobj.company_id.id,
                'picking_type_id': self.env['stock.picking.type'].search([('code', '=', 'outgoing'), ('warehouse_id.company_id.id', '=', sobj.company_id.id)])[0].id,
                'move_lines': [],
                'move_type': 'one',
                'sale_id': sobj.order_id.id,
                'sale_return_id': sobj.id,
                'partner_id': sobj.partner_id.id,
                'origin': sobj.order_id.name,
            }

            if sobj.carrier_id:
                picking_vals['carrier_id'] = sobj.carrier_id.id

            for line in sobj.send_lines:
                picking_vals['move_lines'].append([0, False, {
                    'company_id': sobj.company_id.id,
                    'location_dest_id': location_dest_id,
                    'location_id': location_id,
                    'product_id': line.product_id.id,
                    'name': line.product_id.name,
                    'product_uom_qty': line.product_uom_qty,
                    'product_uom': line.product_id.uom_id.id,
                }])
                if line.price_unit > 0.01:
                    self.env['sale.order.line'].create({
                        'order_id': sobj.order_id.id,
                        'product_id': line.product_id.id,
                        'name': line.product_id.name,
                        'product_uom_qty': line.product_uom_qty,
                        'price_unit': line.price_unit,
                        'tax_id': [[6, 0, [t.id for t in line.tax_id]]]
                    })

            picking_id = self.env['stock.picking'].create(picking_vals)
            # wf_service.trg_validate(uid, 'stock.picking', picking_id, 'button_confirm', cr)

        return self.write({'state': 'processing'})

    @api.one
    def process_payment(self):
        ctx = {
            'sale_order_id': self.order_id.id,
            'sale_return_id': self.id
        }

        if self.balance_remaining <= 0:
            ctx['default_type'] = 'refund'
            ctx['default_amount'] = self.balance_remaining * -1
        else:
            ctx['default_amount'] = self.balance_remaining

        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'sale.order.payment',
            'target': 'new',
            'context': ctx
        }

    @api.multi
    def action_view_delivery(self):
        action = self.env.ref('tradetested.action_stock_picking_delivery_orders')

        result = {
            'name': action.name,
            'help': action.help,
            'type': action.type,
            'view_type': action.view_type,
            'view_mode': action.view_mode,
            'target': action.target,
            'context': action.context,
            'res_model': action.res_model,
        }

        pick_ids = sum([order.picking_ids.ids for order in self], [])

        if len(pick_ids) > 1:
            result['domain'] = "[('id','in',[" + ','.join(map(str, pick_ids)) + "])]"
        elif len(pick_ids) == 1:
            form = self.env.ref('stock.view_picking_form', False)
            form_id = form.id if form else False
            result['views'] = [(form_id, 'form')]
            result['res_id'] = pick_ids[0]
        return result

    @api.one
    def update_status(self):
        all_states = []
        for picking in self.picking_ids:
            all_states.append(picking.state)

        all_states = list(set(all_states))
        if all_states and all_states[0] == 'done' and len(all_states) == 1:
            if self.balance_remaining == 0:
                self.state = 'done'
        elif all_states and all_states[0] == 'cancel' and len(all_states) == 1:
            self.state = 'cancel'

    @api.one
    def button_dummy(self):
        return True

    @api.one
    def button_cancel(self):
        self.state = 'cancel'

    @api.multi
    def open_this(self):
        sobj = self[0]
        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'sale.order.return',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': sobj.id,
        }


class sale_order_return_line(models.Model):
    _name = 'sale.order.return.line'

    return_order_id = fields.Many2one('sale.order.return', 'Return Order', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='return_order_id.order_id.currency_id', store=True, string='Currency', readonly=True)

    company_id = fields.Many2one('res.company', related='return_order_id.company_id', string="Company")
    sale_order_line_id = fields.Many2one('sale.order.line', 'Order Line')
    product_id = fields.Many2one('product.product', 'Product')
    name = fields.Text('Description')
    line_type = fields.Selection([('return', 'Return'), ('send', 'Send')], string='Line Type')
    product_uom_qty = fields.Float('Quantity', digits=dp.get_precision('Product Unit of Measure'), default=1)
    tax_id = fields.Many2many('account.tax', 'sale_order_return_tax', 'order_line_id', 'tax_id', 'Taxes')
    price_unit = fields.Float('Unit Price', required=True, digits=dp.get_precision('Product Price'))

    price_subtotal = fields.Monetary(compute='_compute_amount', string='Subtotal', readonly=True, store=True)
    price_tax = fields.Monetary(compute='_compute_amount', string='Taxes', readonly=True, store=True)
    price_total = fields.Monetary(compute='_compute_amount', string='Total', readonly=True, store=True)

    @api.depends('product_uom_qty', 'price_unit', 'tax_id')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit
            taxes = line.tax_id.compute_all(price, line.return_order_id.currency_id, line.product_uom_qty, product=line.product_id, partner=line.return_order_id.partner_id)
            line.update({
                'price_tax': taxes['total_included'] - taxes['total_excluded'],
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })

    @api.onchange('line_type', 'product_id')
    def onchange_product_id(self):

        order_products = []
        order = self.return_order_id.order_id

        for line in order:
            if line.product_id.type != 'service':
                order_products.append(line.product_id.id)
                for bom in line.product_id.bom_ids:
                    order_products.append(bom.product_id.id)

        self.tax_id = self.product_id.taxes_id if self.product_id.taxes_id else False

        if self.product_id:
            self.name = self.product_id.name

            if self.product_id.id in order_products:
                self.price_unit = 0
            else:
                product = self.product_id.with_context(
                    lang=order.partner_id.lang,
                    partner=order.partner_id.id,
                    quantity=self.product_uom_qty,
                    date=order.date_order,
                    pricelist=order.pricelist_id.id,
                )
                self.price_unit = self.env['account.tax']._fix_tax_included_price(product.price, product.taxes_id, self.tax_id)

    @api.onchange('product_id', 'product_uom_qty', 'tax_id', 'price_unit')
    def onchange_line_vals(self):
        if self.tax_id and self.price_unit and self.product_uom_qty:
            self.price_subtotal = self.tax_id.compute_all(self.price_unit, None, self.product_uom_qty, self.product_id, self.return_order_id.order_id.partner_id)['total_included']


class sale_order_return_update(models.TransientModel):
    _name = 'sale.order.return.update'

    @api.multi
    def update_done(self):
        for obj in self.env['sale.order.return'].browse(self._context['active_ids']):
            if obj.state == 'processing':
                obj.write({'state': 'done'})




