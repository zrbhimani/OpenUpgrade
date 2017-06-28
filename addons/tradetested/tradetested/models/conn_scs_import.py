# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError, except_orm, ValidationError
import StringIO, csv, time, re

carrier_map = {
    'COURIER POST': 'Courierpost',
    'ECL': 'ECL',
    'TOLL TRADETESTED': 'Toll',
    'PBT TRADETESTED': 'PBT',
    'AMF TRADETESTED': 'AMF',
    'COLLECT': 'Pickup',
}


class scs_notification(models.Model):
    _name = 'scs.notification'
    _description = 'SCS Notification'
    _order = 'id desc'

    date = fields.Datetime('Date', default=lambda self: time.strftime('%Y-%m-%d %H:%M:%S'))
    user_id = fields.Many2one('res.users', 'User', default=lambda self: self._uid)
    params = fields.Text('Params', required=True)
    error_msg = fields.Text('Error Message')
    so_id = fields.Many2one('sale.order', string='Sale Order')
    do_id = fields.Many2one('stock.picking', string='Delivery Order')
    dispatch_exception = fields.Boolean(related='do_id.dispatch_exception', string='Delivery Exception')
    dispatch_message = fields.Char(related='do_id.dispatch_message', string="Exception")
    active = fields.Boolean('Active', default=True)
    state = fields.Selection([('draft', 'New'), ('done', 'Processed'), ('error', 'Error')], 'state', default='draft')

    @api.multi
    def process_notification(self):

        for notification in self:
            buffer = StringIO.StringIO(notification.params)
            reader = csv.reader(buffer, delimiter=',', quotechar='"', escapechar="\\")

            soh_row = reader.next()

            if soh_row[0] != 'SOH':
                UserError('First Column of First row must be "SOH"')

            if len(soh_row) != 52:
                UserError('It must have exact 52 columns, in this case its %s' % len(soh_row))

            order_number_parts = soh_row[1].split('/')

            so_num = ''
            do_num = ''
            so_ids = []
            do_ids = []

            if len(order_number_parts) == 3:
                so_num = order_number_parts[0]
                do_num = order_number_parts[1] + '/' + order_number_parts[2]
            elif len(order_number_parts) == 2:
                do_num = order_number_parts[0] + '/' + order_number_parts[1]

            if so_num:
                so_ids = self.env['sale.order'].search([('name', '=', so_num)])
                if not so_ids:
                    raise UserError('Sale Order not Found')

            if do_num:
                do_ids = self.env['stock.picking'].search([('name', '=', do_num)])

            if not do_ids:
                raise UserError('Delivery Order Not Found')

            notification.update({'so_id': so_ids and so_ids[0].id or False, 'do_id': do_ids and do_ids[0].id or False})

            delivery_order = do_ids[0]

            do_items = {}
            delivered_items = []
            for line in delivery_order.move_lines:
                do_items[line.product_id.default_code and line.product_id.default_code.lower()] = line.product_qty

            for row in reader:
                if len(row) != 37:
                    raise UserError('It must have exact 37 columns, in this case its %s' % len(row))

                sku = row[5].lower()
                oqty = int(row[8])
                dqty = int(row[9])

                if sku in do_items:
                    if (oqty == dqty) and (oqty == int(do_items[sku])):
                        do_items.__delitem__(sku)
                        delivered_items.append(row[5] + ' X ' + row[9])
                    else:
                        delivered_items.append(' * ' + row[5] + ' X ' + row[9])

            carrier = soh_row[35].upper()
            dispatch_consignment = soh_row[36]

            if carrier == 'ECL':
                dispatch_consignment = dispatch_consignment[:15]

            if delivery_order.dispatch_consignment and (delivery_order.dispatch_consignment != dispatch_consignment):
                msg = "Carrier Name: <b>" + soh_row[35] + "</b><br/>"
                msg += "Service Type: <b>" + soh_row[50] + "</b><br/>"
                msg += "Consignment Number: <b>" + dispatch_consignment + "</b><br/>"
                msg += "Number of Packets: <b>" + soh_row[30] + "</b><br/>"
                msg += "Number of Pallets: <b>" + soh_row[31] + "</b><br/>"
                msg += "Total Weight: <b>" + soh_row[32] + "</b><br/>"
                msg += "Total Cubic: <b>" + soh_row[33] + "</b><br/>"
                msg += "Freight Cost: <b>" + soh_row[25] + "</b><br/>"
                msg += "Products Delivered: <b>" + "<br/>".join(delivered_items)

                note_vals = {
                    'body': msg,
                    'model': 'stock.picking',
                    'res_id': delivery_order.id,
                    'subtype_id': False,
                    'author_id': self.env.uid.partner_id.id,
                    'type': 'comment',
                }
                self.env['mail.message'].create(note_vals)
            else:
                do_vals = {
                    'dispatch_carrier': soh_row[35],
                    'dispatch_service': soh_row[50],
                    'dispatch_consignment': dispatch_consignment,
                    'carrier_tracking_ref': dispatch_consignment,

                    'dispatch_packets': soh_row[30],
                    'dispatch_pallets': soh_row[31],

                    'dispatch_weight': soh_row[32],
                    'dispatch_cubic': soh_row[33],

                    'freight_cost': soh_row[25],
                    'dispatch_delivered': "\n".join(delivered_items)
                }

                if soh_row[35] not in carrier_map:
                    raise UserError("There isn't any matching entry to this carrier '%s'" % soh_row[35])

                carrier = soh_row[35].upper()
                if carrier == 'ECL':
                    carrier = 'Courierpost'
                elif carrier == 'COLLECT':
                    carrier = False
                else:
                    carrier = carrier_map[soh_row[35].upper()]

                if carrier:
                    carriers = self.env['delivery.carrier'].search([('name', '=', carrier)])
                    if carriers:
                        do_vals['carrier_id'] = carriers[0].id

                delivery_order.write(do_vals)

            delivery_exception = False
            exception_message = ''

            carrier = soh_row[35].upper()
            tracking = soh_row[36]

            delivery_order.refresh()

            amf_zip = False
            if delivery_order.ship_zip or delivery_order.sale_id.ship_zip:
                amf_zip = self.env['amf.postcode'].search([('name', '=', delivery_order.ship_zip or (delivery_order.sale_id and delivery_order.sale_id.ship_zip))])

            # If the SCS Carrier = "COLLECT" and delivery order Carrier <> "Pickup"
            # then put the delivery order in Exception state, output results in Dispatch Results tab
            if carrier == "COLLECT" and delivery_order.carrier_id.name != 'Pickup':
                delivery_exception = True
                exception_message = "Carrier Mismatch"

            # If the SCS Carrier <> "COLLECT" and delivery order Carrier = "Pickup"
            # then put the delivery order in Exception state, output results in Dispatch Results tab
            elif carrier != "COLLECT" and delivery_order.carrier_id.name == 'Pickup':
                delivery_exception = True
                exception_message = "Carrier Mismatch"

            # If delivery order Carrier <> "Pickup" and
            # all of the unit product gross weights in the order are below 30kg and
            # the SCS Carrier is <> "COURIER POST"
            # then put the delivery order in Exception state, output results in Dispatch Results tab
            elif delivery_order.carrier_id.name != 'Pickup' and max([l.product_id.weight for l in delivery_order.move_lines]) < 30 and carrier not in ['COURIER POST', 'ECL']:
                delivery_exception = True
                exception_message = "Weight Less than 30 Kg, and delivery method not COURIER POST or ECL"

            # If delivery order Carrier <> "Pickup" and
            # any of the unit product gross weights in the order is above 30kg and
            # postcode is in the AMF postcodes list and
            # SCS Carrier does not start with "AMF" then put the delivery order in Exception state, output results in Dispatch Results tab
            elif delivery_order.carrier_id.name != 'Pickup' and max([l.product_id.weight for l in delivery_order.move_lines]) > 30 \
                    and amf_zip \
                    and not carrier.startswith('AMF'):
                delivery_exception = True
                exception_message = "Weight greater than 30 Kg, and delivery method not AMF"

            # If delivery order Carrier <> "Pickup" and
            # any of the unit product gross weights in the order is above 30kg and
            # OpenERP product Shipping Group field is "TOLL" or "TOLL_DEPOT" and
            # SCS Carrier does not start with "TOLL" then put the delivery order in Exception state, output results in Dispatch Results tab
            elif delivery_order.carrier_id.name != 'Pickup' and max([l.product_id.weight for l in delivery_order.move_lines]) > 30 \
                    and not amf_zip \
                    and [l.product_id.shipping_group for l in delivery_order.move_lines if l.product_id.shipping_group in ['TOLL', 'TOLL_DEPOT']] \
                    and not carrier.startswith('TOLL'):
                delivery_exception = True
                exception_message = "Weight greater than 30 Kg, and delivery method not TOLL"

            # If the SCS total weight returned is not between delivery order weight -10% and
            # delivery order weight +10% then put the delivery order in Exception state, output results in Dispatch Results tab
            dispatch_weight = delivery_order.dispatch_weight
            delivery_weight = delivery_order.weight
            weight_diff = delivery_weight * 0.1

            if carrier in ['COURIER POST', 'ECL']:
                weight_diff += 1

            if carrier not in ['COLLECT']:
                if (dispatch_weight < (delivery_weight - weight_diff)) or (dispatch_weight > (delivery_weight + weight_diff)):
                    delivery_exception = True
                    exception_message += "\n Weight Exception, Not in range of %s kg - %s kg" % (delivery_weight - weight_diff, delivery_weight + weight_diff)

            dispatch_volume = delivery_order.dispatch_cubic
            delivery_volume = sum([(l.product_id.volume * l.product_qty) for l in delivery_order.move_lines])
            volume_diff_under = delivery_volume * 0.1
            volume_diff_over = delivery_volume * 0.3

            if carrier not in ['COURIER POST', 'ECL', 'COLLECT']:
                if (dispatch_volume < (delivery_volume - volume_diff_under)) or (dispatch_volume > (delivery_volume + volume_diff_over)):
                    delivery_exception = True
                    exception_message += "\n Volume Exception, Not in range of %s cubic - %s cubic" % (delivery_volume - volume_diff_under, delivery_volume + volume_diff_over)

            # If carrier = TOLL TRADETESTED the first 4 chars of the consignment number must be "TRAD".
            if carrier == 'TOLL TRADETESTED':
                if tracking[:4].upper() != 'TRAD':
                    delivery_exception = True
                    exception_message += "Tracking Number '%s' does not match carrier '%s'" % (tracking, carrier)

            # If carrier = PBT TRADETESTED the first 3 chars of the consignment number must be "TRU"
            elif carrier == 'PBT TRADETESTED':
                if tracking[:3].upper() != 'TRU':
                    delivery_exception = True
                    exception_message += "Tracking Number '%s' does not match carrier '%s'" % (tracking, carrier)

            # If carrier = AMF TRADETESTED the first 2 chars of the consignment number must be "TT"
            elif carrier == 'AMF TRADETESTED':
                if tracking[:2].upper() != 'TT':
                    delivery_exception = True
                    exception_message += "Tracking Number '%s' does not match carrier '%s'" % (tracking, carrier)

            # If carrier = COURIER POST the consignment contain the 3 chars "SKY" anywhere in the consignment number
            elif carrier == 'COURIER POST':
                if ('SKY' not in tracking.upper()):
                    delivery_exception = True
                    exception_message += "Tracking Number '%s' does not match carrier '%s'" % (tracking, carrier)

            elif carrier == 'ECL':
                if len(tracking) != 24:
                    delivery_exception = True
                    exception_message += "Tracking Number length is not 24 digits, %s, %s" % (tracking, carrier)
                else:
                    if not tracking[0:15].isdigit() or not tracking[19:21].isdigit() or any(x.isdigit() for x in tracking[16:18] + tracking[22:23]):
                        delivery_exception = True
                        exception_message += "Tracking Number %s seems invalid for %s" % (tracking, carrier)

            if delivery_exception:
                delivery_order.write({'dispatch_exception': True, 'dispatch_message': exception_message.strip()})
                template = self.env['ir.model.data'].xmlid_to_object('tradetested.email_template_outwards_exception')
                mail_id = template.send_mail(delivery_order.id)
            elif (delivery_order.dispatch_exception or delivery_order.dispatch_message):
                delivery_order.update({'dispatch_exception': False, 'dispatch_message': ''})

            notification.update({'state': 'done'})

        return True

    @api.model
    def create(self, vals):
        notification = super(scs_notification, self).create(vals)
        try:
            notification.sudo().process_notification()
        except Exception, e:
            _logger.info(e)
        return notification

    @api.multi
    def open_delivery_order(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Delivery Order',
            'res_model': 'stock.picking',
            'res_id': self.do_id.id,
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('tradetested.view_picking_form').id,
            'target': 'current',
            'nodestroy': True,
        }

    @api.multi
    def open_sale_order(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sales Order',
            'res_model': 'sale.order',
            'res_id': self.so_id.id,
            'view_type': 'form',
            'view_mode': 'form',
            'views': [(self.env.ref('tradetested.view_order_form').id, 'form'), (self.env.ref('tradetested.view_order_tree').id, 'tree')],
            'search_view_id': self.env.ref('tradetested.view_order_filter').id,
            'target': 'current',
            'nodestroy': True,
        }

    @api.multi
    def unlink(self):
        if self._uid != 1:
            raise UserError('Deletion Not Allowed, Only Administrator can delete, Please contact Administrator')
        return super(scs_notification, self).unlink()

    @api.multi
    def set_archived(self):
        return self.write({'active': False})

    @api.multi
    def set_unarchived(self):
        return self.write({'active': True})


class scs_soh(models.Model):
    _name = 'scs.soh'
    _description = 'SCS Stock on Hand'
    _order = 'id desc'
    _log_access = False

    date = fields.Datetime('Date', default=lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'))
    user_id = fields.Many2one('res.users', 'User', default=lambda self: self._uid)
    params = fields.Text('Params', required=True)
    error_msg = fields.Text('Error Message')
    state = fields.Selection([('draft', 'New'), ('done', 'Processed'), ('error', 'Error')], 'state', default='draft')
    comparison_ids = fields.One2many('scs.soh.comparison', 'soh_id', 'Comparison')
    variance_ids = fields.One2many('scs.soh.comparison', 'soh_id', 'Comparison', domain=[('variance', '!=', 0)])

    @api.model
    def create(self, vals):
        res = super(scs_soh, self).create(vals)
        res.process()
        return res

    @api.multi
    def process(self):
        self.ensure_one()

        comp_ids = self.env['scs.soh.comparison'].search([('soh_id', '=', self.id)])
        comp_ids.unlink()

        for line in self.params.split('\n'):
            values = line.split('|')
            if values[0] == 'CODE':
                continue

            if values[5]:
                continue

            prod_id = False
            variance = 0

            self._cr.execute("select id from product_product WHERE lower(default_code) = '%s'" % values[0].lower())
            resp = self._cr.dictfetchone()
            if resp:
                prod_id = resp['id']

            if not prod_id:
                variance = (int(values[6]) - (values[7] and int(values[7]) or 0))
            else:
                prod = self.env['product.product'].browse(prod_id)
                self._cr.execute("""
                                    SELECT
                                    ( SELECT sum(product_qty) from stock_move WHERE product_id = %s and state='done' and location_dest_id=%s )
                                    -
                                    ( SELECT COALESCE(sum(product_qty),0) from stock_move WHERE product_id = %s and state='done' and location_id=%s )
                                    as stock
                    """ % (prod.id, prod.main_location_id.id, prod.id, prod.main_location_id.id))

                resp = self._cr.dictfetchone()
                stock = resp['stock']
                if stock == None:
                    stock = 0

                variance = (int(values[6]) - (values[7] and int(values[7]) or 0)) - stock

            comp_vals = {
                'soh_id': self._ids[0],
                'sku': values[0],
                'description': values[1],
                'grade1': values[2],
                'grade2': values[3],
                'grade3': values[4],
                'status': values[5] and values[5] or False,
                'soh': values[6] and int(values[6]) or 0,
                'commited': values[7] and int(values[7]) or 0,
                'stock': prod_id and stock or 0,
                'variance': variance
            }

            self.env['scs.soh.comparison'].create(comp_vals)
        self.write({'state': 'done'})


class scs_soh_comparison(models.Model):
    _name = 'scs.soh.comparison'
    _description = 'SCS SOH Comparison'
    _log_access = False
    _order = 'variance'

    soh_id = fields.Many2one('scs.soh', 'SOH', ondelete='cascade')
    product_id = fields.Many2one('product.product', compute='_product_id', string="Product")
    sku = fields.Char('SKU')
    timestamp = fields.Datetime('Timestamp', default=lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'))
    description = fields.Char('Description')
    grade1 = fields.Char('Grade 1')
    grade2 = fields.Char('Grade 2')
    grade3 = fields.Char('Grade 3')
    status = fields.Char('Status')
    soh = fields.Integer('SCS Stock')
    commited = fields.Integer('Commited')
    stock = fields.Integer('Our Stock')
    variance = fields.Integer('Variance')

    @api.multi
    @api.depends('sku')
    def _product_id(self):
        for comp in self:
            products = self.env['product.product'].search(['|', ('active', '=', True), ('active', '=', False), ('default_code', '=ilike', comp.sku)])
            if products:
                comp.product_id = products[0].id

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        if self._context.get('most_recent'):
            scs_soh = self.env['scs.soh'].search([])
            if scs_soh:
                args.append(('soh_id', '=', scs_soh[0].id))
        return super(scs_soh_comparison, self).search(args, offset, limit=limit, order=order, count=count)


class process_scs_notification_bulk(models.TransientModel):
    _name = 'process.scs.notification.bulk'

    @api.multi
    def button_archive_bulk(self):
        return self.env['scs.notification'].browse(self._context['active_ids']).write({'active': False})

    @api.multi
    def button_unarchive_bulk(self):
        return self.env['scs.notification'].browse(self._context['active_ids']).write({'active': True})

    @api.multi
    def button_process_bulk(self):
        error_msgs = []
        for notification in self.env['scs.notification'].browse(self._context['active_ids']):
            try:
                notification.process_notification()
            except Exception, e:
                error_msgs.append(str(notification.id) + " : " + str(e))

        self._cr.commit()
        if error_msgs:
            raise UserError("\n".join(error_msgs))
