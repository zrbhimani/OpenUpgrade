# -*- encoding: utf-8 -*-

from odoo import tools, api, fields, models, _
import csv
import cStringIO
import base64
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError



class delivery_order_option(models.TransientModel):
    _name = 'delivery.order.option'

    delivery_option = fields.Char('Delivery Option', char=64)

    @api.model
    def default_get(self, fields):
        if self.env.user.company_id.name != 'Group':
            raise UserError('Only users of "Group" company can use "Deliver" option')

        resp = super(delivery_order_option, self).default_get(fields)
        resp['delivery_option'] = self._context.get('delivery_option', False)
        return resp

    @api.multi
    def set_processing(self):
        for picking in self.env['stock.picking'].browse(self._context.get('active_ids')):
            if picking.state != 'assigned':
                raise UserError("This option can use only when delivery order is in 'Ready to Deliver' stage ")
            picking.write({'processed_date': fields.datetime.now()})
            picking.move_lines.write({'state': 'processing', })
        return True

    @api.multi
    def set_unprocessing(self):
        for picking in self.env['stock.picking'].browse(self._context.get('active_ids')):
            if picking.state != 'processing':
                raise UserError("This option can use only when delivery order is in 'Processing' stage ")
            picking.move_lines.write({'state': 'assigned'})

    @api.multi
    def do_quick_deliver(self):

        for picking in self.env['stock.picking'].browse(self._context.get('active_ids')):
            if picking.state != 'processing':
                raise UserError("This option can use only when delivery order is in 'Processing' stage ")

            for pop in picking.pack_operation_product_ids:
                if pop.qty_done == 0:
                    pop.qty_done = pop.product_qty

            picking.do_new_transfer()

        return True


class internal_move_create(models.TransientModel):
    _name = 'internal.move.create'

    company_id = fields.Many2one('res.company', 'Company')
    move_type = fields.Selection([('Good Stock to Parts Staging', 'Good Stock to Parts Staging'),
                ('Good Stock to Showroom Replenishment', 'Good Stock to Showroom Replenishment'),
                ('Parts Staging to Parts Warehouse', 'Parts Staging to Parts Warehouse')], string="Move Type")
    line_ids = fields.One2many('internal.move.create.line', 'wizard_id', 'Lines')

    low_stock_warning_on_load = fields.Boolean('Low Stock Warning on Load')
    serial_number = fields.Char('Serial Number')
    note = fields.Char('Note')

    @api.multi
    def create_internal_move(self):
        picking_pool = self.env['stock.picking']
        case_pool = self.env['crm.helpdesk']
        location_pool = self.env['stock.location']

        sobj = self[0]

        if not sobj.line_ids:
            raise UserError('Please enter at least one product line')

        for line in sobj.line_ids:
            if line.quantity <= 0:
                raise UserError('Quantity can not be zero')

        location_id = False
        location_dest_id = False
        if sobj.move_type == 'Good Stock to Parts Staging':
            if sobj.company_id.name == 'New Zealand':
                location_id = 12
                location_dest_id = location_pool.search([('name', '=', 'Mezzanine')])[0]
            elif sobj.company_id.name == 'Australia':
                location_id = 15
                location_dest_id = location_pool.search([('name', '=', 'Missing Parts')])[0]
        if sobj.move_type == 'Good Stock to Showroom Replenishment':
            if sobj.company_id.name == 'New Zealand':
                location_id = 12
                location_dest_id = location_pool.search([('location_id.name', '=', 'Auckland Showroom'), ('name', '=', 'Stock')])[0]
        if sobj.move_type == 'Parts Staging to Parts Warehouse':
            if sobj.company_id.name == 'New Zealand':
                location_id = location_pool.search([('name', '=', 'Mezzanine')])[0]
                location_dest_id = location_pool.search([('location_id.name', '=', 'Auckland'), ('name', '=', 'Parts')])[0]

        if not location_id or not location_dest_id:
            raise UserError('Stock Locations can not decide')

        picking_vals = {
            # 'invoice_state': 'none',
            'company_id': sobj.company_id.id,
            'move_lines': [],
            'move_type': 'one'
        }
        if sobj.move_type in ['Parts Staging to Parts Warehouse']:
            picking_vals['type'] = 'parts'
            picking_vals['name'] = self.env['ir.sequence'].next_by_code('stock.picking.parts')
            print picking_vals['name'],"name"
        else:
            picking_vals['type'] = 'internal'

        if self._context.get('active_model') == 'crm.helpdesk':
            case = self.env['crm.helpdesk'].browse(self._context['active_id'])
            picking_vals['case_id'] = case.id

        for line in sobj.line_ids:
            picking_vals['move_lines'].append([0, False, {
                'company_id': sobj.company_id.id,
                'location_dest_id': location_dest_id,
                'location_id': location_id,

                'product_id': line.product_id.id,
                'name': line.product_id.name,

                'product_qty': line.quantity,
                'product_uom': line.product_id.uom_id.id,

                'product_uos_qty': line.quantity,
                'product_uos': False,
            }])

        picking_id = picking_pool.create(picking_vals)
        #picking_pool.draft_force_assign()

        if self._context.get('active_model') == 'crm.helpdesk':
            case_pool.add_related_document([self._context['active_id']], 'stock.picking,' + str(picking_id))

        return {
            'name': 'Internal Moves',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window',
            'res_id': picking_id,
            'target': 'current'
        }

    @api.multi
    def manual_entry(self):
        ir_model_data = self.env['ir.model.data']
        tree_id = ir_model_data.get_object_reference('stock', 'vpicktree')[1]
        form_id = ir_model_data.get_object_reference('stock', 'view_picking_form')[1]

        return {
                    'string'    : 'Internal Moves',
                    'view_type' : 'form',
                    'view_mode' : 'form,tree',
                    'res_model' : 'stock.picking',
                    'type'      : 'ir.actions.act_window',
                    'target'    : 'current',
                    'context'   : {'contact_display': 'partner_address', 'search_default_available': 1},
                    'domain'    : "[('type','=','internal')]",
                    'views'     : [(form_id, 'form')],
        }

    @api.onchange('low_stock_warning_on_load','line_ids')
    def onchange_stock_warning_on_load(self):
        ret = {'value':{}}
        prod_pool = self.env['product.product']
        msgs = ''
        if self.low_stock_warning_on_load and self.line_ids:
            for line in self.line_ids:
                if line[2] and isinstance(line[2], dict):
                    if line[2].get('product_id'):
                        low_stock_msg = prod_pool.low_stock_warning_msg(line[2]['product_id'])
                        if low_stock_msg:
                            msgs += low_stock_msg
        if msgs:
            ret['warning'] = {'title': ('Out of Stock Error!'), 'message' : msgs }

        return ret


class internal_move_create_line(models.TransientModel):
    _name = 'internal.move.create.line'

    wizard_id = fields.Many2one('internal.move.create', 'Wizard')
    product_id = fields.Many2one('product.product', 'Product')
    quantity = fields.Integer('Quantity', default=1)

    @api.onchange('product_id')
    def onchange_product(self):
        ret = {'value':{}}
        if self.product_id:
            product = self.product_id
            if product.type == 'product' and product.saleable_qty <= 0 :
                warning_msgs = "Warning: This Product Is Out Of Stock.        "
                warning_msgs += "\nOn Hand: %s units" %product.qty_available
                warning_msgs += "\nSaleable: %s units" %product.saleable_qty

                if product.po_expected_qty:
                    warning_msgs += "\n\nExpected Date: %s" %datetime.strptime(product.po_expected_date,'%Y-%m-%d').strftime('%d %b %Y')
                    warning_msgs += "\nExpected Quantity: %s" %product.po_expected_qty

                ret.update({'warning': {'title': ('Out of Stock Error!'), 'message' : warning_msgs }})
        return ret


class export_incoming_shipment(models.TransientModel):
    _name = 'export.incoming.shipment'

    file = fields.Binary('Exported File')
    file_name = fields.Char('File Name', size=256)

    @api.multi
    def export_csv(self):
        buf = cStringIO.StringIO()
        sobj = self[0]

        headers = ['SKU', 'Description', 'Gross Weight', 'Volume', 'Carton Quantity', 'Total Quantity', 'Internal Notes']
        buf.write(",".join(headers) + "\n")

        for id in self._context.get('active_ids'):
            for pick in self.env['stock.picking'].browse([id]):
                for move in pick.move_lines:
                    val = [move.product_id.default_code.encode('utf-8'), move.product_id.name.encode('utf-8'), str(move.product_id.weight), str(move.product_id.volume), str(move.product_id.carton_qty), str(move.product_qty),
                           pick.note or '']
                    buf.write(",".join(val) + "\n")

        file = base64.encodestring(buf.getvalue())
        buf.close()
        file_name = 'Incoming_Shipments_' + time.strftime('%d_%b_%H_%M_%p')
        self.write({'file_name': file_name + '.csv', 'file': file})

        return {
            'name': 'Export CSV',
            'type': 'ir.actions.act_window',
            'res_model': 'export.incoming.shipment',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': sobj.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

class return_to_parts(models.TransientModel):
    _name = 'return.to.parts'

    picking_id = fields.Many2one('stock.picking', 'Picking')
    product_id = fields.Many2one('product.product', 'Product')
    note = fields.Text('Note')
    mode = fields.Selection([('return_to_parts', 'Return to Parts'), ('move_to_parts', 'Move to Parts'),
                              ('move_to_mezzanine', 'Move to Mezzanine')], string="Mode")

    @api.model
    def default_get(self, fields):
        resp = super(return_to_parts, self).default_get(fields)
        resp['picking_id'] = self._context['active_id']
        return resp

    @api.multi
    def return_to_parts(self):
        sobj = self[0]
        pick_pool = self.env['stock.picking']

        loc_part_ids = self.env['stock.location'].search([('name', '=', 'Parts')])
        if not loc_part_ids:
            raise UserError('Parts Location', 'Please create location "Parts"')

        orig_move = False
        for move in sobj.picking_id.move_lines:
            if move.product_id.id == sobj.product_id.id:
                orig_move = move
                break;

        if orig_move.product_qty - orig_move.parts_qty <= 0:
            raise UserError('Cannot move', 'All quantities already moved to parts')

        picking_vals = {
            'name': self.env['ir.sequence'].get('stock.picking'),
            'origin': sobj.picking_id.name,
            'type': 'internal',
            'company_id': sobj.picking_id.company_id.id,
            'parts_move': True,
            'note': sobj.note,
            'move_lines': [[0, False, {
                'company_id': sobj.product_id.company_id.id,
                'location_dest_id': loc_part_ids[0],
                'location_id': orig_move.location_dest_id.id,
                'product_id': sobj.product_id.id,
                'name': sobj.product_id.name,
                'product_qty': 1,
                'product_uom': sobj.product_id.uom_id.id,
                'product_uos_qty': 1,
                'product_uos': False,
            }]],
        }

        picking_id = pick_pool.create(picking_vals)

        pick_pool.draft_force_assign()
        picking = pick_pool.browse(picking_id)
        if picking.state == 'confirmed':
            picking.force_assign()

        orig_move.write({'parts_qty': orig_move.parts_qty + 1})

        user = self.env.user
        vals = {
            'body': u'<strong>Returned to Parts %s</strong><br/><p>%s</p>' % (sobj.picking_id.name, sobj.note),
            'model': 'stock.picking',
            'res_id': picking_id,
            'subtype_id': False,
            'author_id': user.partner_id.id,
            'type': 'comment'
        }

        self.env['mail.message'].create(vals)

        vals = {
            'body': u'<strong>Returned to Parts %s</strong><br/><p>%s</p>' % (picking.name, sobj.note),
            'model': 'stock.picking',
            'res_id': sobj.picking_id.id,
            'subtype_id': False,
            'author_id': user.partner_id.id,
            'type': 'comment'
        }

        self.env['mail.message'].create(vals)

        return {
            'name': 'Internal Moves',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window',
            'res_id': picking_id,
            'target': 'current'
        }

    @api.multi
    def move_to_parts(self):
        sobj = self[0]
        product = sobj.picking_id.move_lines[0].product_id

        move_vals = {
            'picking_id': sobj.picking_id.id,
            'company_id': sobj.picking_id.company_id.id,
            'location_dest_id': self.env['stock.location'].search([('name', '=', 'Parts')])[0],
            'location_id': self.env['stock.location'].search([('name', '=', 'Mezzanine')])[0],
            'product_id': product.id,
            'name': product.name,
            'product_qty': 1,
            'product_uom': product.uom_id.id,
            'product_uos_qty': 1,
            'product_uos': False,
            'state': 'assigned'
        }

        self.env['stock.move'].create(move_vals)

        sobj.picking_id.write({'state': 'assigned'})

        vals = {
            'body': u'<strong>Move to Parts</strong><br/><p>%s</p>' % (sobj.note),
            'model': 'stock.picking',
            'res_id': sobj.picking_id.id,
            'subtype_id': False,
            'author_id': self.env['res.users'].browse(self._uid).partner_id.id,
            'type': 'comment'
        }

        self.env['mail.message'].create(vals)

        return True

    @api.multi
    def move_to_mezzanine(self):
        sobj = self[0]
        product = sobj.picking_id.move_lines[0].product_id

        move_vals = {
            'picking_id': sobj.picking_id.id,
            'company_id': sobj.picking_id.company_id.id,
            'location_dest_id': self.env['stock.location'].search([('name', '=', 'Mezzanine')])[
                0],
            'location_id': self.env['stock.location'].search([('name', '=', 'Parts')])[0],
            'product_id': product.id,
            'name': product.name,
            'product_qty': 1,
            'product_uom': product.uom_id.id,
            'product_uos_qty': 1,
            'product_uos': False,
            'state': 'assigned'
        }
        self.env['stock.move'].create(move_vals)

        sobj.picking_id.write({'state': 'assigned'})

        vals = {
            'body': u'<strong>Move to Mezzanine</strong><br/><p>%s</p>' % (sobj.note),
            'model': 'stock.picking',
            'res_id': sobj.picking_id.id,
            'subtype_id': False,
            'author_id': self.env['res.users'].browse(self._uid).partner_id.id,
            'type': 'comment'
        }

        self.env['mail.message'].create(cr, uid, vals)

        return True



# class stock_partial_move_line(models.TransientModel):
#     _inherit = 'stock.partial.move.line'
#
#     total_cost = fields.Float("Total")
#
#     @api.onchange('quantity','cost')
#     def onchange_cost(self):
#         ret = {'value': {'total_cost': 0}}
#
#         if self.quantity and self.cost:
#             ret['value']['total_cost'] = self.quantity * self.cost
#
#         return ret
#
#
# class stock_partial_move(models.Model):
#     _inherit = 'stock.partial.move'
#
#     @api.model
#     def default_get(self, fields):
#         resp = super(stock_partial_move, self).default_get(fields)
#         if resp and 'move_ids' in resp:
#             for line in resp['move_ids']:
#                 line['total_cost'] = line.get('cost', 0) * line['quantity']
#         return resp