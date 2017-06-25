# -*- encoding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, except_orm, ValidationError
from odoo.addons import decimal_precision as dp
from odoo.tools.float_utils import float_compare

import time
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pytz
import logging
import tt_fields
import uuid
import common

_logger = logging.getLogger(__name__)


class stock_picking(models.Model):
    _name = 'stock.picking'
    _order = 'id desc'
    _inherit = ['stock.picking', 'mail.thread', 'base.activity', 'obj.watchers.base']

    def _default_uom(self):
        uom_categ_id = self.env.ref('product.product_uom_categ_kgm').id
        return self.env['product.uom'].search([('category_id', '=', uom_categ_id), ('factor', '=', 1)], limit=1)

    dispatch_message = fields.Char('Dispatch Message')
    dispatch_exception = fields.Boolean('Exception', default=False)
    tracking_status = fields.Selection([('New', 'New'), ('In Transit', 'In Transit'), ('Complete', 'Complete')], string='Tracking Status')
    carrier_tracking_ref = fields.Char(string='Consignment Number', copy=False)
    carrier_id = fields.Many2one("delivery.carrier", string="Carrier")
    tracking_last_up = fields.Datetime('Tracking Last Update')
    sale_return_id = fields.Many2one('sale.order.return', 'Return Order')
    date_order = fields.Date(related='sale_id.date_order', store=True, string="Order Date")

    url = fields.Char('Tracking URL', size=1024)
    phone = fields.Char('Phone')
    delivery_instructions = fields.Text('Delivery Instructions', size=2048)
    processed_date = fields.Datetime('Processed Date')
    create_date = fields.Datetime('Created Date', readonly=True, index=True)
    origin_so = fields.Integer(string="dummy no use")
    is_group_company = fields.Boolean(compute='_is_group_company', string="is Group Company")
    is_picking_lines_readonly = fields.Boolean(compute='_is_picking_lines_readonly', string="Readonly")
    file_name = fields.Char(compute='_file_name', size=256, string="File Name")
    channel = fields.Selection(related='sale_id.channel',
                               selection=[('not_defined', 'Not defined'), ('phone', 'Phone'), ('showroom', 'Showroom'), ('website', 'Website'), ('trademe', 'Trade Me'), ('ebay', 'eBay'), ('daily_deal', 'Daily Deal'),
                                          ('wholesale', 'Wholesale')], string='Channel')
    user_id = fields.Many2one(related='sale_id.user_id', relation='res.users', string='Salesperson')
    freight_cost = fields.Float('Freight Cost')

    ship_tt_company_name = fields.Char('Company', size=64)
    ship_street = fields.Char('Street', size=128)
    ship_street2 = fields.Char('Street2', size=128)
    ship_zip = fields.Char('Zip', change_default=True, size=24)
    ship_city = fields.Char('City', size=128)
    ship_state_id = fields.Many2one("res.country.state", 'State')
    ship_country_id = fields.Many2one('res.country', 'Country')
    last_ship_address = fields.Boolean('Last Ship Address')
    sale_address = fields.Text(compute='_sale_address', string="Delivery Address")

    state = fields.Selection(compute='_state_get', copy=False,
                             selection=[
                                 ('draft', 'Draft'),
                                 ('cancel', 'Cancelled'),
                                 ('waiting', 'Waiting Another Operation'),
                                 ('confirmed', 'Waiting Availability'),
                                 ('partially_available', 'Partially Available'),
                                 ('assigned', 'Available'),
                                 ('processing', 'Processing'),
                                 ('done', 'Done'),
                             ], string='Status', readonly=True, index=True, track_visibility='onchange', store=True)

    dispatch_carrier = fields.Char('Carrier Name', size=255)
    dispatch_service = fields.Char('Service Type', size=255)
    dispatch_consignment = fields.Char('Consignment Number', size=255)
    dispatch_packets = fields.Integer('Number of Packets')
    dispatch_pallets = fields.Integer('Number of Pallets')
    dispatch_weight = fields.Float('Total Weight')
    dispatch_cubic = fields.Float('Total Cubic', digits=(20, 4))
    dispatch_delivered = fields.Text('Delivered')

    due_date = fields.Datetime(compute='_due_date', string="Due Date", store=True)
    weight = fields.Float(compute='_cal_weight_and_volume', string='Weight', digits=dp.get_precision('Stock Weight'), multi='_cal_weight', store=True)
    weight_net = fields.Float(compute='_cal_weight_and_volume', string='Net Weight', digits=dp.get_precision('Stock Weight'), multi='_cal_weight', store=True)
    volume = fields.Float(compute='_cal_weight_and_volume', string='Volume', digits=(20, 4), multi='_cal_weight', store=True)
    own_id = fields.Many2one('stock.picking', compute='_own_deliveries', string='Tracking Number', store=True)
    tracking_notes = fields.Text('Tracking Notes')
    case_id = fields.Many2one('crm.helpdesk', 'Case')
    case_order_id = fields.Many2one('sale.order', related='case_id.order_id', string='Sale Order')

    sale_phone = fields.Char(related='sale_id.phone', string="SO Phone")
    show_split = fields.Boolean(compute='_show_split', string="Show Split")
    carrier_tracking_url = fields.Char(related='carrier_id.tracking_url', string="Tracking URL", readonly=True)
    carrier_price = fields.Float(string="Shipping Cost", readonly=True)
    delivery_type = fields.Selection(related='carrier_id.delivery_type', readonly=True)
    number_of_packages = fields.Integer(string='Number of Packages', copy=False)
    weight_uom_id = fields.Many2one('product.uom', string='Unit of Measure', required=True, readonly="1", help="Unit of measurement for Weight", default=_default_uom)

    guid = tt_fields.Uuid('GUID')
    is_confirm_deliver = fields.Boolean('Confirm & Deliver button Clicked')

    type = fields.Selection([('out', 'Sending Goods'), ('in', 'Getting Goods'), ('internal', 'Internal'), ('parts', 'Parts')], 'Shipping Type', index=True, help="Shipping type specify, goods coming in or going out.")

    parts_move = fields.Boolean('Parts Move')
    parts_move_location = fields.Char(compute='_parts_move_location', string='Parts move Location')

    part_product_id = fields.Many2one('product.product', compute='_cal_product_qty_dest', string='Product')
    part_product_qty = fields.Float(compute='_cal_product_qty_dest', string='Quantity')
    part_location_dest_id = fields.Many2one('stock.location', compute='_cal_product_qty_dest', string='Destination Location')

    sale_id = fields.Many2one('sale.order', 'Sales Order', store=True)

    _sql_constraints = [
        ('guid_uniq', 'unique(guid)', 'Unique GUID is required'),
    ]

    @api.multi
    def _is_group_company(self):
        if self.env.user.company_id.name == 'Group':
            for picking in self:
                picking.is_group_company = True

    @api.multi
    def _is_picking_lines_readonly(self):
        user_dc = self.env.user.has_group('tradetested.group_stock_dispatch')
        for picking in self:
            if picking.state=='done' and not user_dc:
                picking.is_picking_lines_readonly = True

    @api.multi
    def _file_name(self):
        for picking in self:
            picking.file_name = (picking.sale_id.name + '_' if picking.sale_id else '') + picking.name.replace('/', '_')

    @api.multi
    def _sale_address(self):
        for picking in self:
            if picking.carrier_id.name == 'Pickup' and (picking.ship_street or picking.ship_street2 or picking.phone or picking.ship_tt_company_name):
                address_format = '<span style="color:blue">%(phone)s<br/>%(tt_company_name)s<br/>%(delivery_instructions)s</span>'
                address_obj = picking
            elif picking.carrier_id.name == 'Pickup' and picking.sale_id:
                address_format = "%(phone)s<br/>%(tt_company_name)s<br/>%(delivery_instructions)s"
                address_obj = picking.sale_id
            elif picking.ship_street or picking.ship_street2 or picking.phone or picking.ship_tt_company_name:
                address_format = '<span style="color:blue">%(phone)s<br/>%(tt_company_name)s<br/>%(street)s<br/>%(street2)s<br/>%(city)s,%(state_code)s %(zip)s<br/>%(country_name)s<br/><br/>%(delivery_instructions)s</span>'
                address_obj = picking
            elif picking.sale_id:
                address_format = "%(phone)s<br/>%(tt_company_name)s<br/>%(street)s<br/>%(street2)s<br/>%(city)s,%(state_code)s %(zip)s<br/>%(country_name)s<br/><br/>%(delivery_instructions)s"
                address_obj = picking.sale_id
            else:
                continue
            picking.sale_address = address_format % {
                'phone': address_obj.phone or '',
                'tt_company_name': address_obj.ship_tt_company_name or '',
                'street': address_obj.ship_street or '',
                'street2': address_obj.ship_street2 or '',
                'city': address_obj.ship_city or '',
                'zip': address_obj.ship_zip or '',
                'state_code': address_obj.ship_state_id and address_obj.ship_state_id.code or '',
                'country_name': address_obj.ship_country_id and address_obj.ship_country_id.name or '',
                'delivery_instructions': address_obj.delivery_instructions and address_obj.delivery_instructions or ''
            }

    @api.depends('move_lines.state')
    def _state_get(self):
        res = {}
        for pick in self:
            if not pick.move_lines:
                pick.state = pick.launch_pack_operations and 'assigned' or 'draft'
                continue
            if any([x.state == 'draft' for x in pick.move_lines]):
                pick.state = 'draft'
                continue
            if all([x.state == 'cancel' for x in pick.move_lines]):
                pick.state = 'cancel'
                continue
            if all([x.state in ('cancel', 'done') for x in pick.move_lines]):
                pick.state = 'done'
                continue
            if all([x.state == 'processing' for x in pick.move_lines]):
                pick.state = 'processing'
                continue

            order = {'confirmed': 0, 'waiting': 1, 'assigned': 2}
            order_inv = {0: 'confirmed', 1: 'waiting', 2: 'assigned'}
            lst = [order[x.state] for x in pick.move_lines if x.state not in ('cancel', 'done')]
            if pick.move_type == 'one':
                pick.state = order_inv[min(lst)]
            else:
                # we are in the case of partial delivery, so if all move are assigned, picking
                # should be assign too, else if one of the move is assigned, or partially available, picking should be
                # in partially available state, otherwise, picking is in waiting or confirmed state
                pick.state = order_inv[max(lst)]
                if not all(x == 2 for x in lst):
                    if any(x == 2 for x in lst):
                        pick.state = 'waiting'
                    else:
                        # if all moves aren't assigned, check if we have one product partially available
                        for move in pick.move_lines:
                            if move.partially_available:
                                pick.state = 'waiting'
                                break
        return res

    @api.depends('date')
    def _due_date(self):
        for picking in self:
            picking.due_date = (datetime.strptime(picking.date, '%Y-%m-%d %H:%M:%S') + relativedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')

    @api.depends('move_lines.product_id', 'move_lines')
    def _cal_weight_and_volume(self):
        for picking in self:
            weight = weight_net = volume = 0.00
            for move in picking.move_lines:
                weight += move.weight
                weight_net += move.weight_net
                volume += move.volume
            picking.update({
                'weight': weight,
                'weight_net': weight_net,
                'volume': volume,
            })

    @api.model
    def _own_deliveries(self):
        for id in self.ids:
            id.own_id = id

    @api.multi
    def _show_split(self):
        for picking in self:
            if picking.state not in ['draft', 'confirmed', 'assigned'] \
                    or not picking.move_lines \
                    or (len(picking.move_lines) == 1 and picking.move_lines[0].product_qty <= 1):
                picking.show_split = False
            else:
                picking.show_split = True

    @api.multi
    def do_partial(self, partial_datas):
        move_obj = self.env['stock.move']
        product_obj = self.env['product.product']
        currency_obj = self.env['res.currency']
        uom_obj = self.env['product.uom']
        sequence_obj = self.env['ir.sequence']
        wf_service = netsvc.LocalService("workflow")
        for pick in self:
            new_picking = None
            complete, too_many, too_few = [], [], []
            move_product_qty, prodlot_ids, product_avail, partial_qty, product_uoms = {}, {}, {}, {}, {}
            for move in pick.move_lines:
                if move.state in ('done', 'cancel'):
                    continue
                partial_data = partial_datas.get('move%s' % (move.id), {})
                product_qty = partial_data.get('product_qty', 0.0)
                move_product_qty[move.id] = product_qty
                product_uom = partial_data.get('product_uom', False)
                product_price = partial_data.get('product_price', 0.0)
                product_currency = partial_data.get('product_currency', False)
                prodlot_id = partial_data.get('prodlot_id')
                prodlot_ids[move.id] = prodlot_id
                product_uoms[move.id] = product_uom
                partial_qty[move.id] = uom_obj._compute_qty(product_uoms[move.id], product_qty, move.product_uom.id)
                if move.product_qty == partial_qty[move.id]:
                    complete.append(move)
                elif move.product_qty > partial_qty[move.id]:
                    too_few.append(move)
                else:
                    too_many.append(move)

                # Average price computation
                if (pick.type == 'in') and (move.product_id.cost_method == 'average'):
                    product = product_obj.browse(move.product_id.id)
                    move_currency_id = move.company_id.currency_id.id
                    self._context['currency_id'] = move_currency_id
                    qty = uom_obj._compute_qty(product_uom, product_qty, product.uom_id.id)

                    if product.id in product_avail:
                        product_avail[product.id] += qty
                    else:
                        product_avail[product.id] = product.qty_available

                    if qty > 0:
                        new_price = currency_obj.compute(product_currency,
                                                         move_currency_id, product_price)
                        new_price = uom_obj._compute_price(product_uom, new_price,
                                                           product.uom_id.id)
                        if product.qty_available <= 0:
                            new_std_price = new_price
                        else:
                            # Get the standard price
                            amount_unit = product.price_get('standard_price')[product.id]
                            new_std_price = ((amount_unit * product_avail[product.id]) \
                                             + (new_price * qty)) / (product_avail[product.id] + qty)
                        # Write the field according to price type field
                        product_obj.write([product.id], {'standard_price': new_std_price})

                        # Record the values that were chosen in the wizard, so they can be
                        # used for inventory valuation if real-time valuation is enabled.
                        move_obj.write([move.id],
                                       {'price_unit': product_price,
                                        'price_currency_id': product_currency})

            for move in too_few:
                product_qty = move_product_qty[move.id]
                if not new_picking:
                    new_picking_name = pick.name
                    self.write([pick.id],
                               {'name': sequence_obj.get(
                                   'stock.picking.%s' % (pick.type)),
                               })
                    new_picking = self.copy(pick.id,
                                            {
                                                'name': new_picking_name,
                                                'move_lines': [],
                                                'state': 'draft',
                                            })
                if product_qty != 0:
                    defaults = {
                        'product_qty': product_qty,
                        'product_uos_qty': product_qty,
                        'picking_id': new_picking,
                        'state': 'assigned',
                        'move_dest_id': False,
                        'price_unit': move.price_unit,
                        'product_uom': product_uoms[move.id]
                    }
                    prodlot_id = prodlot_ids[move.id]
                    if prodlot_id:
                        defaults.update(prodlot_id=prodlot_id)
                    move_obj.copy(move.id, defaults)
                move_obj.write([move.id],
                               {
                                   'product_qty': move.product_qty - partial_qty[move.id],
                                   'product_uos_qty': move.product_qty - partial_qty[move.id],
                                   'prodlot_id': False,
                                   'tracking_id': False,
                               })

            if new_picking:
                move_obj.write([c.id for c in complete], {'picking_id': new_picking})
            for move in complete:
                defaults = {'product_uom': product_uoms[move.id], 'product_qty': move_product_qty[move.id]}
                if prodlot_ids.get(move.id):
                    defaults.update({'prodlot_id': prodlot_ids[move.id]})
                move_obj.write([move.id], defaults)
            for move in too_many:
                product_qty = move_product_qty[move.id]
                defaults = {
                    'product_qty': product_qty,
                    'product_uos_qty': product_qty,
                    'product_uom': product_uoms[move.id]
                }
                prodlot_id = prodlot_ids.get(move.id)
                if prodlot_ids.get(move.id):
                    defaults.update(prodlot_id=prodlot_id)
                if new_picking:
                    defaults.update(picking_id=new_picking)
                move_obj.write([move.id], defaults)

            if self._context.get('default_mode') == 'split':
                return {'delivered_picking': new_picking}

            if new_picking:
                wf_service.trg_validate('stock.picking', new_picking, 'button_confirm')
                self.write([pick.id], {'backorder_id': new_picking})
                self.action_move([new_picking])
                wf_service.trg_validate('stock.picking', new_picking, 'button_done')
                wf_service.trg_write('stock.picking', pick.id)
                delivered_pack_id = new_picking
                back_order_name = self.browse(delivered_pack_id).name
                self.message_post(body=_("Back order <em>%s</em> has been <b>created</b>.") % (back_order_name))
            else:
                self.action_move([pick.id])
                wf_service.trg_validate('stock.picking', pick.id, 'button_done')
                delivered_pack_id = pick.id

            delivered_pack = self.browse(delivered_pack_id)
            pick.delivered_pack = {'delivered_picking': delivered_pack.id or False}

    @api.multi
    def _parts_move_location(self):
        for picking in self:
            if picking.parts_move or picking.type == 'parts':
                states = [m.state for m in picking.move_lines]
                states = list(set(states))
                if len(states) == 1 and states[0] == 'done':
                    picking.parts_move_location = picking.move_lines[0].location_dest_id.name
        return

    @api.multi
    def _cal_product_qty_dest(self):
        for picking in self[0]:
            if picking.move_lines:
                picking.part_product_id = picking.move_lines[0].product_id.id
                picking.part_product_qty = picking.move_lines[0].product_qty
                picking.part_location_dest_id = picking.move_lines[0].location_dest_id.id
        return

    # TODO Check picking.type
    #
    # @api.multi
    # def draft_validate(self):
    #     self.write({'is_confirm_deliver': True})
    #     return super(stock_picking, self).draft_validate()
    #
    # @api.multi
    # def check_deliver(self):
    #     for do in self:
    #         if do.state == 'processing' and do.dispatch_carrier == 'COLLECT' and do.carrier_id.name == 'Pickup' and do.carrier_tracking_ref and do.dispatch_delivered and '*' not in do.dispatch_delivered and not do.dispatch_exception:
    #             wiz_id = self.env['delivery.order.option'].create({})
    #             self.env['delivery.order.option'].do_quick_deliver([wiz_id])
    # self.env['delivery.order.option'].do_quick_deliver(cr, uid, [wiz_id], context={'active_model': 'stock.picking.out', 'active_ids': [do.id]})
    #
    # @api.multi
    # def action_done(self):
    #     """Changes picking state to done.
    #
    #     This method is called at the end of the workflow by the activity "done".
    #     @return: True
    #     """
    #     self.write({'state': 'done', 'date_done': time.strftime('%Y-%m-%d %H:%M:%S')})
    #     for picking in self:
    #         self.env['sale.order'].update_sale_order_status([picking.sale_id.id])
    #         if picking.sale_return_id:
    #             self.env['sale.order.return'].update_status([picking.sale_return_id.id])
    #     return True


    # OnChange
    @api.onchange('ship_state_id')
    def onchange_ship_state(self):
        if self.ship_state_id:
            self.ship_country_id = self.ship_state_id.country_id.id

    @api.onchange('carrier_id', 'carrier_tracking_ref')
    def onchange_carrier_tracking_ref(self):
        res = {'value': {}}
        res['value']['tracking_url'] = "http://www.tradetested.co.nz/support/tracking?carrier=%s&ref=%s" % (self.carrier_id.name if self.carrier_id else '', self.carrier_tracking_ref)
        if self.carrier_tracking_ref:
            args = [('carrier_tracking_ref', '=', self.carrier_tracking_ref)]
            if self.id:
                args.append(('id', '!=', self.id))
            found_pickings = self.search(args)
            res['warning'] = {
                'title': 'Warning! Tracking number already used',
                'message': 'This tracking number has already been used on delivery order: %s.\n'
                           'Are you sure you want to continue?' % (", ".join([p.name for p in found_pickings]))
            }
        return res

    # Actions
    @api.multi
    def open_tracking_url(self):
        self.ensure_one()
        if self.carrier_id and self.carrier_id.tracking_url and self.carrier_tracking_ref:
            return {
                'name': 'Go to website',
                'res_model': 'ir.actions.act_url',
                'type': 'ir.actions.act_url',
                'target': 'new',
                'url': self.carrier_id.tracking_url.replace('{TRACKING_REF}', self.carrier_tracking_ref)
            }

    @api.multi
    def open_picking(self):
        self.ensure_one()
        return {
            'name': 'Delivery Order',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window',
            'res_id': self.id,
            'target': 'current'
        }

    @api.multi
    def action_cancel(self):
        resp = super(stock_picking, self).action_cancel()
        user_datetime = common.convert_tz(datetime.today(), self.env.user.tz)
        msg_body = u'<p>Delivery Order cancelled by %s %s' % (self.env.user.name, user_datetime.strftime('%d/%m/%Y %I:%M %p %Z'))
        self.log_note(body=msg_body)
        return resp

    @api.multi
    def action_process_auto(self):
        # For Server Actions
        stock_picking = self.pool.get('stock.picking')

        for picking in self.browse(cr, uid, ids):
            vals = {'delivery_date': time.strftime('%Y-%m-%d')}

            for move in picking.move_lines:
                vals['move%s' % (move.id)] = {
                    'product_id': move.product_id.id,
                    'product_qty': move.product_qty,
                    'product_uom': move.product_uom.id,
                    'prodlot_id': move.prodlot_id.id,
                }

            stock_picking.do_partial(cr, uid, [picking.id], vals, context=context)

        return True

    # Operations
    @api.multi
    def button_split(self):
        self._context.update({
            'active_model': self._name,
            'active_ids': self._ids,
            'active_id': len(self._ids) and self._ids[0] or False,
            'default_mode': 'split'
        })
        return {
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'stock.partial.picking',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': self._context,
            'nodestroy': True,
        }

    @api.multi
    def ignore_exception(self):
        return self.write({'dispatch_exception': False})

    @api.multi
    def do_split(self):
        if not [pack.qty_done for pack in self.pack_operation_ids if pack.qty_done > 0]:
            raise UserError("Please specify split qty in 'Operations' tab's 'Done' column")

        for pack in self.pack_operation_ids:
            if pack.qty_done > 0:
                pack.product_qty = pack.qty_done
            else:
                pack.unlink()
        return self.with_context({'do_only_split': True}).do_transfer()

    # Connection
    @api.multi
    def pushto_busdo(self, old_states=None):
        old_states = old_states or {}

        for picking in self:

            if (picking.state == old_states.get(picking.id) == 'assigned') \
                    or (picking.picking_type_id.code not in ['outgoing', 'internal']) \
                    or (picking.picking_type_id.code == 'outgoing' and not picking.sale_id) \
                    or picking.is_confirm_deliver:
                continue

            address_container = picking.sale_id if picking.sale_id else picking

            msg = {
                "guid": picking.guid or None,
                "name": picking.name,
                "type": picking.picking_type_id.code,
                "state": picking.state,
                "origin": picking.origin or None,
                "tracking_reference": picking.carrier_tracking_ref or None,
                "sales_order_guid": address_container.guid,
                "delivery_instructions": address_container.delivery_instructions or None,
                "signature_opt_out": picking.sale_id.signature_opt_out,
                "carrier": {"id": picking.carrier_id.id, "name": picking.carrier_id.name} if picking.carrier_id else None,
                "customer": {
                    "name": picking.partner_id.name or None,
                    "phone": picking.partner_id.phone or None,
                    "company_name": picking.partner_id.tt_company_name or None
                },
                "shipping_address": {
                    "company": address_container.ship_tt_company_name or '',
                    "street": [address_container.ship_street or '', address_container.ship_street2 or ''],
                    "city": address_container.ship_city or '',
                    "postcode": address_container.ship_zip or '',
                    "region": address_container.ship_state_id.code if address_container.ship_state_id else None,
                    "telephone": address_container.phone or '',
                },
                "items": [],
            }
            for line in picking.move_lines:
                if picking.type == 'internal' \
                        or line.location_id.complete_name == "Physical Locations / New Zealand / Auckland / Stock":
                    msg["items"].append({
                        "move_line_id": line.id,
                        "sku": line.product_id.default_code,
                        "product_guid": line.product_id.guid,
                        "quantity_ordered": line.product_qty,
                        "name": line.name,
                        "source_location": {"id": line.location_id.id, "name": line.location_id.complete_name},
                        "shipping_group": line.product_id.shipping_group or None,
                        "destination_location": {"id": line.location_dest_id.id or None, "name": line.location_dest_id.complete_name or None},
                        "item_weight": line.product_id.weight,
                        "item_volume": line.product_id.volume,
                    })
            self.env['rabbitmq'].push(queue='odoo.bus.delivery_orders', message=msg)
        return True

    @api.multi
    def export_to_scs(self):
        picking = self[0]
        data = {}
        if picking.picking_type_id.code != 'outgoing':
            _logger.warning("Export to SCS aborted: Not Delivery Order")
            return

        # Check to see if Newzealand Order
        is_nz = False
        for mv_line in picking.move_lines:
            if mv_line.location_id.complete_name == 'Physical Locations / New Zealand / Auckland / Stock':
                is_nz = True

        if not is_nz:
            _logger.warning("Export to SCS aborted: Source Location is not NZ/Auckland")
            return True

        # Assign Some details
        data['picking_id'] = picking.id
        data['unique_number'] = picking.origin + '/' + picking.name if picking.name and picking.origin else picking.name

        data['warehouse_note'] = False
        data['date'] = False

        data['ship_name'] = picking.partner_id.name

        # check to see if pickup order
        is_pickup = False
        if picking.carrier_id and picking.carrier_id.name == 'Pickup':
            is_pickup = True
        elif picking.sale_id:
            for so_line in picking.sale_id.order_line:
                if so_line.product_id and so_line.product_id.name == 'Pickup':
                    is_pickup = True

        if not is_pickup and not (picking.ship_street or picking.sale_id.ship_street):
            is_pickup = True

        if is_pickup:
            data['ship_street'] = 'Pickup',
            data['priority'] = '4',
            data['ship_suburb'] = 'Pickup',
            data['ship_city'] = 'Pickup',

        elif picking.ship_street or picking.ship_street2 or picking.phone or picking.ship_tt_company_name:
            data['ship_street'] = picking.ship_street or ''
            data['ship_street'] += picking.ship_street2 and (', ' + picking.ship_street2) or ''
            data['ship_city'] = picking.ship_city or ''
            data['ship_zip'] = picking.ship_zip or ''
            data['ship_suburb'] = picking.ship_city or ''
            data['phone'] = picking.phone or False

            data['instructions'] = ''
            if picking.ship_tt_company_name:
                data['tt_company_name'] = picking.ship_tt_company_name
                data['instructions'] += picking.ship_tt_company_name
            if picking.phone:
                if data['instructions']:
                    data['instructions'] += ', '
                data['instructions'] += 'Ph: ' + str(picking.phone)
            if picking.delivery_instructions:
                if data['instructions']:
                    data['instructions'] += ' - '
                data['instructions'] += common.clean(picking.delivery_instructions)

        elif picking.sale_id:
            data['ship_street'] = picking.sale_id.ship_street or ''
            data['ship_street'] += picking.sale_id.ship_street2 and (', ' + picking.sale_id.ship_street2) or ''
            data['ship_city'] = picking.sale_id.ship_city or ''
            data['ship_zip'] = picking.sale_id.ship_zip or ''
            data['ship_suburb'] = picking.sale_id.ship_city or ''
            data['phone'] = picking.sale_id.phone or False

            data['instructions'] = ''
            if picking.sale_id.ship_tt_company_name:
                data['tt_company_name'] = picking.sale_id.ship_tt_company_name
                data['instructions'] += picking.sale_id.ship_tt_company_name
            if picking.sale_id.phone:
                if data['instructions']:
                    data['instructions'] += ', '
                data['instructions'] += 'Ph: ' + str(picking.sale_id.phone.encode('ascii', 'ignore'))
            if picking.sale_id.delivery_instructions:
                if data['instructions']:
                    data['instructions'] += ' - '
                data['instructions'] += common.clean(picking.sale_id.delivery_instructions)
            if picking.sale_id.signature_opt_out:
                if data['instructions']:
                    data['instructions'] += ' - '
                data['instructions'] += 'Signature not required'

        data['lines'] = []

        for mv_line in picking.move_lines:
            if mv_line.location_id.complete_name == 'Physical Locations / New Zealand / Auckland / Stock':
                data['lines'].append({
                    'line_number': mv_line.id,
                    'sku': mv_line.product_id.default_code,
                    'qty': int(mv_line.product_qty),
                })

        if picking.priority in ['3', '2']:  # Very Urgent, Urgent Respectively
            data['priority'] = int(picking.priority) + 1

        return self.env['scs.export.api'].dispatch_request(data)

    @api.multi
    def export_to_scs_internal(self):
        picking = self[0]
        data = {}

        if picking.picking_type_id.code != 'internal':
            _logger.warning("Export to SCS aborted: Not Internal Move")
            return

        # Check to see if Newzealand Order
        is_nz = False
        for mv_line in picking.move_lines:
            if mv_line.location_id.complete_name == 'Physical Locations / New Zealand / Auckland / Stock':
                is_nz = True

        if not is_nz:
            _logger.warning("Export to SCS aborted: Source Location is not NZ/Auckland")
            return True

        # Assign Some details
        data['picking_id'] = picking.id
        data['unique_number'] = picking.name
        data['warehouse_note'] = False
        data['date'] = False
        data['ship_name'] = 'Trade Tested'

        # check to see if pickup order
        if picking.carrier_id and picking.carrier_id.name == 'Pickup':
            data['ship_street'] = 'Pickup'
            data['priority'] = '4'
            data['ship_suburb'] = 'Pickup'
            data['ship_city'] = 'Pickup'
        else:
            dest_location_parts = mv_line.location_dest_id.complete_name.split(' / ')
            dest_location = " / ".join(dest_location_parts[-2:])
            data['ship_street'] = dest_location
            data['ship_suburb'] = dest_location
            data['ship_city'] = dest_location
            data['ship_zip'] = ''
            data['phone'] = False
            data['instructions'] = ''

        data['lines'] = []

        for mv_line in picking.move_lines:
            if mv_line.location_id.complete_name == 'Physical Locations / New Zealand / Auckland / Stock':
                data['lines'].append({
                    'line_number': mv_line.id,
                    'sku': mv_line.product_id.default_code,
                    'qty': int(mv_line.product_qty),
                })

        return self.env['scs.export.api'].dispatch_request(data)

    # ORM
    @api.model
    def create(self, vals):
        if not vals.get('guid'):
            vals['guid'] = uuid.uuid4()
        vals = common.strip_sale_address(vals)
        if vals.get('carrier_tracking_ref'):
            vals['carrier_tracking_ref'] = vals['carrier_tracking_ref'].strip()
        return super(stock_picking, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = common.strip_sale_address(vals)
        if vals.get('carrier_tracking_ref'):
            vals['carrier_tracking_ref'] = vals['carrier_tracking_ref'].strip()

        old_states = [(p.id, p.state) for p in self]
        resp = super(stock_picking, self).write(cr, uid, ids, vals, context=context)

        if 'state' in vals or 'carrier_tracking_ref' in vals:
            self.pushto_busdo(old_states=old_states)
        return super(stock_picking, self).write(vals)

    @api.multi
    def copy(self, default=None):
        default = default or {}
        default['guid'] = uuid.uuid4()

        if not self.env.user.has_group('tradetested.group_stock_dispatch'):
            raise osv.except_orm('Access Error', 'Only "Dispatch Coordinator" users can duplicate')

        return super(stock_picking, self).copy(default)


class stock_move(models.Model):
    _inherit = 'stock.move'
    _company_loc = {}

    product_id = fields.Many2one('product.product', 'Product', required=True, index=True, domain=[('type', '<>', 'service')])
    po_expected_date = fields.Date(related='product_id.po_expected_date', string="PO Expected Date")
    product_qty = fields.Float('Quantity', digits=dp.get_precision('Product Unit of Measure'),
                               required=False,
                               help="This is the quantity of products from an inventory "
                                    "point of view. For moves in the state 'done', this is the "
                                    "quantity of products that were actually moved. For other "
                                    "moves, this is the quantity of product that is planned to "
                                    "be moved. Lowering this quantity does not generate a "
                                    "backorder. Changing this quantity on assigned moves affects "
                                    "the product reservation, and should be done with care."
                               )
    order_ref = fields.Char(compute='_get_ref', store=True, string='Order #', multi="ref")
    picking_ref = fields.Char(compute='_get_ref', store=True, string='Reference', multi="ref")
    cost_price = fields.Float('Cost Price')
    cost_sign = fields.Integer(compute='_cost_sign', string='Cost Sign', store=True)
    is_profit = fields.Boolean(compute='_check_profit_sales', string="Profit Sales", store=True)
    sale_line_id = fields.Many2one('sale.order.line', 'Order Line')
    default_loc = fields.Boolean(compute='is_default_loc_move')
    picking_state = fields.Selection(related='picking_id.state',
                                     selection=[('draft', 'Draft'),
                                                ('auto', 'Waiting Another Operation'),
                                                ('confirmed', 'Waiting Availability'),
                                                ('assigned', 'Ready to Deliver'),
                                                ('processing', 'Processing'),
                                                ('done', 'Delivered'),
                                                ('cancel', 'Cancelled'), ],
                                     string='Picking Status')

    weight = fields.Float(compute='_cal_move_weight_volume', digits=dp.get_precision('Stock Weight'), store=True)
    weight_net = fields.Float(compute='_cal_move_weight_volume', digits=dp.get_precision('Stock Weight'), store=True)
    volume = fields.Float(compute='_cal_move_weight_volume', digits=(16,3), store=True)

    @api.depends('product_id', 'product_uom_qty', 'product_uom')
    def _cal_move_weight_volume(self):
        for move in self:
            move.update({
                'weight': move.product_qty * move.product_id.weight,
                'weight_net': move.product_qty * move.product_id.weight_net,
                'volume': move.product_qty * move.product_id.volume,
            })

    @api.multi
    @api.depends('picking_id', 'picking_id.sale_id')
    def _check_profit_sales(self):
        for move in self:
            move.is_profit = False
            if move.picking_id and move.picking_id.sale_id:
                for line in move.picking_id.sale_id.order_line:
                    if line.product_id and line.product_id.type != 'service' and line.product_uom_qty > 0 and (line.price_subtotal / line.product_uom_qty) >= line.product_id.standard_price:
                        if line.product_id.bom_ids:  # and line.product_id.supply_method == 'produce'
                            for bom in line.product_id.bom_ids:
                                if move.product_id.id in [l.product_id.id for l in bom.bom_line_ids]:
                                    move.is_profit = True
                        elif move.product_id.id == line.product_id.id:
                            move.is_profit = True

    @api.multi
    def _cost_sign(self):
        for move in self:
            if move.picking_id and move.picking_id.picking_type_code == 'outgoing':
                move.cost_sign = 1
            elif move.picking_id and move.picking_id.picking_type_code == 'incoming':
                move.cost_sign = -1

    @api.multi
    def _get_ref(self):
        for move in self:
            move_vals = {'order_ref': False, 'picking_ref': False}
            if move.picking_id:
                move.picking_ref = move.picking_id.name
                if move.picking_id.sale_id:
                    move.order_ref = move.picking_id.sale_id.name
                elif move.picking_id.purchase_id:
                    move.order_ref = move.picking_id.purchase_id.name

    @api.multi
    def is_default_loc_move(self):
        warehouse_pool = self.env['stock.warehouse']
        for move in self:
            if move.product_id.company_id:
                if move.product_id.company_id.id not in self._company_loc:
                    warehouse_ids = warehouse_pool.search([('company_id', '=', move.product_id.company_id.id)])
                    print warehouse_ids, "warehouse id"
                    if warehouse_ids:
                        print self._company_loc, "loc"
                        print move.product_id.company_id.id, "com id"
                        print warehouse_pool.browse(warehouse_ids[0]), "browese"
                        print warehouse_pool.browse(warehouse_ids[0])
                        self._company_loc[move.product_id.company_id.id] = warehouse_pool.browse(warehouse_ids[0])
                if self._company_loc[move.product_id.company_id.id] == move.location_id.id or self._company_loc[move.product_id.company_id.id] == move.location_dest_id.id:
                    move.default_loc = True

    @api.model
    def create(self, vals):
        if vals.get('picking_id'):
            data = {'user_id': self._uid, 'date': datetime.now(), 'res_id': vals['picking_id'], 'res_model': 'stock.picking', 'activity': 'Lines : Added', 'value_before': ''}
            if vals.get('product_id'):
                prod = self.env['product.product'].browse(vals['product_id'])
                data['value_after'] = (prod.default_code or prod.name) + ' X ' + str(int(vals['product_uom_qty']))
            else:
                data['value_after'] = '"' + vals['name'] + '"' + ' X ' + str(int(vals['product_uom_qty']))
            self.env['res.activity.log'].create(data)

        return super(stock_move, self).create(vals)

    @api.multi
    def write(self, vals):
        for line in self:
            if ('product_id' in vals) or ('product_uom_qty' in vals) or ('picking_id' in vals):
                if 'picking_id' in vals:
                    data = {'user_id': self._uid, 'date': datetime.now(), 'activity': 'Lines : Created', 'res_id': vals['picking_id'], 'res_model': 'stock.picking', 'value_before': ''}
                else:
                    data = {'user_id': self._uid, 'date': datetime.now(), 'activity': 'Lines : Updated', 'res_id': line.picking_id.id, 'res_model': 'stock.picking',
                            'value_before': (line.product_id.default_code or (line.product_id.name or line.name)) + ' X ' + str(int(line.product_uom_qty))}

                if 'product_id' in vals:
                    line_prod = self.env['product.product'].browse(vals['product_id'])
                else:
                    line_prod = line.product_id

                if 'product_uom_qty' in vals:
                    line_qty = vals['product_uom_qty']
                else:
                    line_qty = line.product_uom_qty

                data['value_after'] = (line_prod.default_code or (line_prod.name or line_prod.name)) + ' X ' + str(int(line_qty))
                self.env['res.activity.log'].create(data)

                # line.product_id.invalidate_stock_cache()

        resp = super(stock_move, self).write(vals)

        # Setting Picking's Priorities
        if 'picking_id' in vals:
            picking = self.env['stock.picking'].browse(vals['picking_id'])
            if picking.carrier_id.name == 'Pickup':
                picking.priority = '3'  # Very Urgent
            elif not picking.carrier_id or picking.carrier_id.name == 'Delivery NZ':
                if not [l.id for l in picking.move_lines if l.product_id.weight > 25 or l.product_id.volume > 0.25]:
                    picking.priority = '2'  # Urgent

            #
            if picking.picking_type_id.code in ['outgoing', 'internal']:
                for move in self:
                    if move.product_id.type == 'product' and move.product_id.saleable_qty <= 0:
                        warning_msgs = "Warning: <b>[%s] %s</b> is out of stock.  <br/>" % (move.product_id.default_code, move.product_id.name)
                        warning_msgs += "On Hand: %s units. Saleable: %s units <br/>" % (move.product_id.qty_available, move.product_id.saleable_qty)
                        if move.product_id.po_expected_qty:
                            warning_msgs += "Expected %s units on %s" % (move.product_id.po_expected_qty, datetime.strptime(move.product_id.po_expected_date, '%Y-%m-%d').strftime('%d %b %Y'))

                        msg_vals = {
                            'body': warning_msgs,
                            'model': 'stock.picking',
                            'res_id': move.picking_id.id,
                            'subtype_id': False,
                            'author_id': self.env.user.partner_id.id,
                            'type': 'comment'
                        }
                        self.env['mail.message'].create(msg_vals)

        # Pushing to MQ
        if vals.get('state') == 'assigned':
            done_pickings = []
            for line in self:
                if line.picking_id and line.picking_id.picking_type_id.code == 'outgoing' and line.picking_id.state == 'assigned' and line.picking_id.id not in done_pickings:
                    line.picking_id.pushto_busdo()

        return resp

    @api.multi
    def unlink(self):
        for line in self:
            if line.picking_id:
                data = {'res_model': 'stock.picking', 'user_id': self._uid, 'date': datetime.now(), 'activity': 'Lines : Deleted', 'value_after': '', 'res_id': line.picking_id.id}
                if line.product_id:
                    data['value_before'] = (line.product_id.default_code or line.product_id.name) + ' X ' + str(int(line.product_uom_qty))
                else:
                    data['value_before'] = line.name + ' X ' + str(int(line.product_uom_qty))
                self.env['res.activity.log'].create(data)
        return super(stock_move, self).unlink()


class stock_inventory(models.Model):
    _inherit = 'stock.inventory'

    sale_order_line_id = fields.Many2one('sale.order.line', 'Sale Order Line')

    @api.multi
    def action_confirm(self):
        # to perform the correct inventory corrections we need analyze stock location by
        # location, never recursively, so we use a special context
        product_context = dict(self._context, compute_child=False)

        location_obj = self.env['stock.location']
        for inv in self:
            move_ids = []
            for line in inv.inventory_line_id:
                pid = line.product_id.id
                product_context.update(uom=line.product_uom.id, to_date=inv.date, date=inv.date, prodlot_id=line.prod_lot_id.id)
                amount = location_obj._product_get(line.location_id.id, [pid], product_context)[pid]
                change = line.product_qty - amount
                lot_id = line.prod_lot_id.id
                if change:
                    location_id = line.product_id.property_stock_inventory.id
                    value = {
                        'name': 'INV:' + (line.inventory_id.name or ''),
                        'product_id': line.product_id.id,
                        'product_uom': line.product_uom.id,
                        'prodlot_id': lot_id,
                        'date': inv.date,
                        'company_id': line.inventory_id.company_id.id
                    }

                    if change > 0:
                        value.update({
                            'product_qty': change,
                            'location_id': location_id,
                            'location_dest_id': line.location_id.id,
                        })
                    else:
                        value.update({
                            'product_qty': -change,
                            'location_id': line.location_id.id,
                            'location_dest_id': location_id,
                        })
                    move_ids.append(self._inventory_line_hook(line, value))
            self.write({'state': 'confirm', 'move_ids': [(6, 0, move_ids)]})
            if not move_ids:
                raise except_orm('No Stock Moves', 'no change in stock counts, so can not post inventory.')
            self.env['stock.move'].action_confirm()
        return True


class procurement_order(models.Model):
    _inherit = 'procurement.order'

    @api.multi
    def message_post(self, body='', subject=None, message_type='notification', subtype=None, parent_id=False, attachments=None, content_subtype='html', **kwargs):
        pass  # Ignore message posting on procurement orders
        return True

    @api.multi
    def _prepare_purchase_order(self, partner):
        resp = super(procurement_order, self)._prepare_purchase_order(partner)
        if self.sale_line_id:
            # Setting Dropshiment PO's Sale order
            resp['sale_id'] = self.sale_line_id.order_id.id
        return resp
