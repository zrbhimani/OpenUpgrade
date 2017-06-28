# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
import time
from datetime import datetime, timedelta
from odoo.exceptions import UserError
from odoo.addons.tradetested.models.common import convert_tz
from odoo.tools.safe_eval import safe_eval
import base64, csv, StringIO
from nameparser.parser import HumanName
import logging

_logger = logging.getLogger(__name__)

def get_next_weekday(weekday):
    """
        @startdate: given date, in format '2013-05-25'
        @weekday: week day as a integer, between 0 (Monday) to 6 (Sunday)
    """
    d = datetime.today()
    t = timedelta( 7 + ( (7 + weekday - d.weekday()) % 7))
    return (d + t).strftime('%Y-%m-%d')


class case_rural_delivery(models.TransientModel):
    _name = 'case.rural.delivery'

    @api.multi
    def create_case_view(self):
        case_id = self.create_case()
        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'crm.helpdesk',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': case_id,
        }

    @api.multi
    def create_case(self):
        # Sales team
        team_pool = self.env['crm.team']
        team_ids = team_pool.search([('name', 'ilike', 'Customer Service')])

        # Category - Issue
        categ_pool = self.env['crm.lead.tag']
        categ_ids = categ_pool.search([('object_id.model', '=', 'crm.helpdesk'), ('name', 'ilike', 'Rural Delivery')])

        # Create Case
        case_pool = self.env['crm.helpdesk']
        case_id = case_pool.create( {
            'name': 'Rural Delivery',
            'ref': self._context.get('active_id') and 'sale.order,' + str(self._context['active_id']) or False,
            'section_id': team_ids and team_ids[0] or False,
            'date_deadline': time.strftime('%Y-%m-%d'),
            'categ_id': categ_ids and categ_ids[0] or False,
            'user_id': False
        })
        # Update order state to waiting state
        self.env['sale.order'].write([self._context.get('active_id', 0)], {'order_status': 'waiting_address'})

        # Open Case  - This will set responsible user also 
        case_id.case_open(self._context)

        # Manually remove the Responsible user
        case_id.write({'user_id': False})

        return case_id


class case_out_of_stock(models.TransientModel):
    _name = 'case.out.of.stock'

    @api.multi
    def create_case_view(self):
        case_id = self.create_case()
        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'crm.helpdesk',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': case_id,
        }

    @api.model
    def create_case(self):
        # Sales team
        team_pool = self.env['crm.team']
        team_ids = team_pool.search([('name', 'ilike', 'Customer Service')])

        # Category - Issue
        categ_pool = self.env['crm.lead.tag']
        categ_ids = categ_pool.search([('object_id.model', '=', 'crm.helpdesk'), ('name', 'ilike', 'Out Of Stock')])

        # Create Case
        case_pool = self.env['crm.helpdesk']
        case_id = case_pool.create({
            'name': 'Out Of Stock',
            'ref': self._context.get('active_id') and 'sale.order,' + str(self._context['active_id']) or False,
            'section_id': team_ids and team_ids[0] or False,
            'date_deadline': time.strftime('%Y-%m-%d'),
            'categ_id': categ_ids and categ_ids[0] or False,
        })
        # Update order state to waiting state
        # self.pool.get('sale.order').write(cr, uid, [context.get('active_id',0)], {'order_status': 'waiting_stock'})

        # Open Case
        case_id.case_open()

        # Manually remove the Responsible user
        case_id.write({'user_id': False})

        return case_id


class create_part_order_case(models.TransientModel):
    _name = 'create.part.order.case'

    order_id = fields.Many2one('sale.order', 'Sale Order')
    product_ids = fields.Many2many('product.product', 'rel_part_order_case_product', 'wiz_id', 'product_id', 'Product IDS')
    product_id = fields.Many2one('product.product', 'Product')
    description = fields.Text('Description')

    @api.multi
    def generate_description(self,order, product_name='[PRODUCT]'):
        description = 'Can you please arrange parts order as follows\n\n'

        for line in order.order_line:
            if line.product_id:
                continue

            description += str(line.product_uom_qty) + ' x ' + line.name + '\n'

        description += '\n\n'
        description += 'Original product purchased:\n' + product_name
        description += '\n\n'

        description += 'Deliver to:\n'

        description += order.partner_id.name + '\n'
        description += (order.phone or '') + '\n'
        description += order.tt_company_name and (order.tt_company_name + '\n') or ''
        description += order.ship_street and (order.ship_street + '\n') or ''
        description += order.ship_street2 and (order.ship_street2 + '\n') or ''
        description += (order.ship_city and (order.ship_city + ',') or '') + (order.ship_state_id and order.ship_state_id.code + ',' or '') + (order.ship_zip and order.ship_zip or '') + '\n'
        description += (order.ship_country_id and order.ship_country_id.name or '') + '\n\n'

        description += order.delivery_instructions or '' + '\n'

        return description

    @api.model
    def default_get(self, field):
        resp = super(create_part_order_case, self).default_get(field)

        order = self.env['sale.order'].browse(self._context['active_id'])
        resp['product_ids'] = []
        resp['order_id'] = self._context['active_id']
        for line in order.order_line:
            if line.product_id:
                resp['product_ids'].append(line.product_id.id)

        for rel_order in order.related_orders:
            for rel_line in rel_order.order_line:
                resp['product_ids'].append(rel_line.product_id.id)

        resp['description'] = self.generate_description(order)

        return resp

    @api.multi
    def create_case(self):
        sobj = self[0]
        case_pool = self.env['crm.helpdesk']
        order = self.env['sale.order'].browse(self._context['active_id'])

        now_local = convert_tz(datetime.today(), 'Pacific/Auckland')

        if (now_local.weekday() <= 2) and (now_local.hour < 12):
            # Friday of this week
            days_to_add = 4 - now_local.weekday()
            deadline = now_local + timedelta(days=days_to_add)
        else:
            days_to_add = (7 + 4) - now_local.weekday()
            deadline = now_local + timedelta(days=days_to_add)

        vals = {
            'name': '%s - Parts order' % order.name,
            'owner_id': 21,
            'user_id': 21,
            'section_id': 2,
            'company_id': order.company_id and order.company_id.id or False,

            'date_deadline': deadline,
            'priority': '3',
            # 'categ_id'      : 21,
            'resolution_id': 1,

            'ref': 'sale.order,' + str(order.id),
            'ref2': 'product.product,' + str(sobj.product_id.id),
            'description': sobj.description.replace('\n', '<br/>'),
        }
        case_id = case_pool.create(vals)
        case_id.case_open()
        return True

    @api.onchange('product_id','product_ids','order_id')
    def onchange_product(self):
        ret_val = {}
        if self.order_id:
            order = self.env['sale.order'].browse(self.order_id)
            product_name = '[PRODUCT]'
            if self.product_id:
                product = self.env['product.product'].browse(self.product_id)
                product_name = (product.default_code and ('[' + product.default_code + '] ') or '') + product.name

            ret_val['description'] = self.generate_description(order, product_name)

        return {'value': ret_val, 'domain': {'product_id': [('id', 'in', self.product_ids[0][2]), ('type', '!=', 'service')]}}


class depot_delivery(models.TransientModel):
    _name = 'depot.delivery'

    @api.multi
    def _get_partner_id(self):
        for delivery in self:
            delivery.partner_id_dummy = delivery.partner_id.id

    @api.multi
    def _set_partner_id(self, value):
        if value:
            self.write({'partner_id': value})
        return True

    partner_id = fields.Many2one('res.partner', 'Partner', domain=[('is_depot', '=', True)])
    country_id = fields.Many2one('res.country', 'Country')
    partner_id_dummy = fields.Many2one('res.partner', compute='_get_partner_id', fnct_inv=_set_partner_id, string="Partner", domain=[('is_depot', '=', True)])

    @api.model
    def default_get(self, fields):
        resp = super(depot_delivery, self).default_get(fields)
        order = self.env['sale.order'].browse(self._context['active_id'])
        resp['country_id'] = order.warehouse_id.company_id.country_id.id
        return resp

    @api.multi
    def change_address(self):
        sobj = self[0]
        if sobj.partner_id:
            order = self.env['sale.order'].browse(self._context.get('active_id'))
            vals = {
                'ship_tt_company_name': sobj.partner_id.tt_company_name,
                'ship_street': sobj.partner_id.street,
                'ship_street2': sobj.partner_id.street2,
                'ship_city': sobj.partner_id.city,
                'ship_zip': sobj.partner_id.zip,
                'ship_country_id': sobj.partner_id.country_id and sobj.partner_id.country_id.id or False,
                'delivery_instructions': "Call customer on %s when ready for collection" % (order.phone)
            }
            order.write(vals)
        return True


class merge_orders(models.TransientModel):
    _name = 'merge.orders'

    related_orders = fields.Many2many('sale.order', 'merge_sale_order_rel', string='Related Orders')
    message = fields.Text('Message')
    not_allowed = fields.Boolean('No Merge')

    @api.model
    def default_get(self, fields):
        res = {}
        order = self.env['sale.order'].browse(self.env.context.get('active_id'))
        if order.state not in ['draft', 'sent']:
            res['message'] = 'Only draft sale orders can merge'
            res['not_allowed'] = True
        else:
            merge_orders = filter(lambda x: x.state in ['draft', 'sent'], order.related_orders)
            if not merge_orders:
                res['message'] = "No related draft sale orders"
                res['not_allowed'] = True
            else:
                draft_orders = [x.id for x in order.related_orders if x.state in ['draft', 'sent']]
                res['message'] = "This will merge " + str(len(draft_orders)) + " draft sale orders in to this order, Continue?"
                res['not_allowed'] = False
                res['related_orders'] = [x.id for x in merge_orders]
        return res

    @api.multi
    def merge_orders(self):
        line_pool = self.env['sale.order.line']

        order_obj = self.env['sale.order'].browse(self._context.get('active_id'))
        order_lines = {}
        msgs = []
        for line in order_obj.order_line:
            order_lines[line.id] = {'line_id': line.id, 'product_id': line.product_id.id, 'name': line.name, 'qty': line.product_uom_qty, 'price': line.price_unit}
        sobj = self[0]
        for order in sobj.related_orders:
            # order line copied
            for line in order.order_line:
                line_updated = False
                for ol_id, ol in order_lines.items():
                    if line.product_id.name in ['Delivery NZ', 'Delivery AU', 'Delivery USA'] and ol['name'] == line.name:
                        line_pool.write({'price_unit': ol['price'] + line.price_unit})
                        order_lines[ol_id]['price'] = ol['price'] + line.price_unit
                        line_updated = True
                    elif ol['product_id'] == line.product_id.id and ol['name'] == line.name and ol['price'] == line.price_unit:
                        line_pool.write({'product_uom_qty': ol['qty'] + line.product_uom_qty})
                        order_lines[ol_id]['qty'] = ol['qty'] + line.product_uom_qty
                        line_updated = True

                if not line_updated:
                    line_id = line.copy({'order_id': order_obj.id})
                    order_lines[line_id] = {'product_id': line.product_id.id, 'name': line.name, 'qty': line.product_uom_qty, 'price': line.price_unit}


            # move Cases
            if order.case_ids:
                search_term = 'sale.order,' + str(order.id)
                new_term = 'sale.order,' + str(order_obj.id)

                self._cr.execute("UPDATE crm_helpdesk set ref='%s' where ref='%s' and id in (%s)" % (new_term, search_term, ",".join(map(str, [x.id for x in order.case_ids]))))
                self._cr.execute("UPDATE crm_helpdesk set ref2='%s' where ref2='%s' and id in (%s)" % (new_term, search_term, ",".join(map(str, [x.id for x in order.case_ids]))))
                self._cr.execute("UPDATE crm_helpdesk set ref3='%s' where ref3='%s' and id in (%s)" % (new_term, search_term, ",".join(map(str, [x.id for x in order.case_ids]))))

            # move Payments
            for payment in order.sale_order_payment_id:
                payment.write({'sale_order_id': order_obj.id})

            # Cancel the Order
            order.write({'state': 'cancel'})

            msgs.append(u'<b>Merged Order %s and Order %s</b> ' % (order_obj.name, order.name))

            note_vals = {
                'body': u'<b>Merged Order %s and Order %s</b> ' % (order_obj.name, order.name),
                'model': 'sale.order',
                'res_id': order.id,
                'subtype_id': False,
                'author_id': self.env['res.users'].browse().partner_id.id,
                'type': 'comment',
            }
            self.env['mail.message'].create(note_vals)


        # Update Sequence to move Delivery in last
        order_obj.refresh()
        for line in order_obj.order_line:
            if line.product_id.name in ['Delivery NZ', 'Delivery AU', 'Delivery USA']:
                line.write({'sequence': 100})

        note_vals = {
            'body': "<br/>".join(msgs),
            'model': 'sale.order',
            'res_id': order_obj.id,
            'subtype_id': False,
            'author_id': self.env['res.users'].browse().partner_id.id,
            'type': 'comment',
        }
        self.env['mail.message'].create(note_vals)

        return True


class cancel_order(models.TransientModel):
    _name = 'cancel.order'
    _columns = {}

    # def button_cancel_order(self):
    #     self.env['sale.order'].action_cancel([self._context.get('active_id')])
    #     return True

    @api.multi
    def button_cancel_order(self):
        if self._context.get('cancel_button') == 'workflow_cancel':
            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate('sale.order', self._context.get('active_id'), 'cancel')

        elif self._context.get('cancel_button') == 'object_action_cancel':
            self.env['sale.order'].action_cancel([self._context.get('active_id')])

        else:
            self.env['sale.order'].action_cancel([self._context.get('active_id')])

        return True


class sale_quick_exchange(models.TransientModel):
    _name = 'sale.quick.exchange'

    case_id = fields.Many2one('crm.helpdesk', 'Case')
    order_id = fields.Many2one('sale.order', 'Sale Order')
    company_id = fields.Many2one('res.company', related='order_id.company_id', string="Company")
    product_ids = fields.Many2many('product.product', 'sale_quick_exchange_prod_rel', 'wizard_id', 'product_id', 'Products')
    transfer_amount = fields.Float('Transfer Amount')
    deadline = fields.Date('Case Deadline')
    notes = fields.Text('Notes for Case')

    @api.multi
    def create_new_order(self):

        order_pool = self.env['sale.order']
        line_pool = self.env['sale.order.line']
        case_pool = self.env['crm.helpdesk']

        sobj = self[0]

        if sobj.transfer_amount <= 0:
            raise UserError(_('Insufficient Data'), _("Transfer Amount is Required"))
        elif sobj.transfer_amount > sobj.order_id.payments_total_less_refunds:
            raise UserError("Transfer Amount is More than Balance")

        default_data = {
            'name': '/',
            'state': 'draft',
            'message_ids': [],
            'picking_ids': [],
            'log_ids': [],
            'sale_order_payment_id': [],
            'order_line': [],

            'order_status': False,
            'channel': False,
            'pick_date': False,
            'ship_date': False,
            'ship_via': False,
            'tracking_number': False,
            'date_followup_email': False,

            'date_payment_confirm': False,
            'date_payment_reminder': False,
            'date_payment_reminder_2': False,
            'date_pickup_email': False,
            'email_status_update': False,
            'cancel_case_date': False,
            'mailchimp_export_date': False,
            'date_feedback': False,
            'magento_order_number': False,
            'trademe_purchase_id': False,
            'trademe_listing_id': False,
            'trademe_username': False,
            'trademe_sale_type': False,
            'trademe_pay_now_purchase': False
        }

        order_data = order_pool.copy_data(sobj.order_id.id, default=default_data)

        new_order_id = order_pool.create(order_data)
        new_order = order_pool.browse(new_order_id)

        for prod in sobj.product_ids:
            line_pool.create({
                'order_id': new_order_id,
                'product_id': prod.id,
                'name': prod.name,
                'product_uom_qty': 1,
                'tax_id': [[6, 0, [t.id for t in prod.taxes_id]]],
                'price_unit': prod.list_price
            })

        if sobj.case_id:
            case_pool.add_related_document([sobj.case_id.id], 'sale.order,' + str(new_order_id))

        payment_pool = self.env['sale.order.payment']
        payment_pool.create({
            'type': 'refund',
            'method': 'transfer',
            'amount': abs(sobj.transfer_amount),
            'sale_order_id': sobj.order_id.id,
            'comment': 'Transfer to Order %s' % new_order.name
        })

        payment_pool.create({
            'type': 'payment',
            'method': 'transfer',
            'amount': abs(sobj.transfer_amount),
            'sale_order_id': new_order.id,
            'comment': 'Transfer From Order %s' % sobj.order_id.name
        })

        categ_pool = self.env['crm.lead.tag']
        resol_pool = self.env['crm.helpdesk.resolution']
        user = self.env['res.users'].browse()
        case_pool.create({
            'owner_id': self._uid,
            'user_id': self._uid,
            'categ_id': 20,
            'resolution_ids': [[6, 0, resol_pool.search([('name', '=', 'Exchange')])]],
            'description': 'Quick Exchange created by ' + user.name + '. Needs investigation and tidy up. <br/><br/>' + sobj.notes,
            'date_deadline': time.strftime('%Y-%m-%d'),

            'ref': 'sale.order,%s' % sobj.order_id.id,
            'ref2': 'sale.order,%s' % new_order_id
        })

        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'sale.order',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': new_order_id,
        }

    @api.onchange('product_ids')
    def onchange_product(self):
        ret = {'value': {}}
        warning_msgs = u""
        if self.product_ids[0][2]:
            for product in self.env['product.product'].browse([self.product_ids[0][2][-1]]):
                if product.type == 'product' and product.saleable_qty <= 0:
                    warning_msgs += u"\nWarning: %s Is Out Of Stock." % product.default_code
                    warning_msgs += u"\nOn Hand: %s units" % product.qty_available
                    warning_msgs += u"\nSaleable: %s units" % product.saleable_qty

                    if product.po_expected_qty:
                        warning_msgs += u"\n\nExpected Date: %s" % datetime.strptime(product.po_expected_date, '%Y-%m-%d').strftime('%d %b %Y')
                        warning_msgs += u"\nExpected Quantity: %s" % product.po_expected_qty

        if warning_msgs:
            ret.update({'warning': {'title': ('Out of Stock Error!'), 'message': warning_msgs}})
        return ret


class cancel_and_create_order(models.TransientModel):
    _name = 'cancel.and.create.order'

    @api.multi
    def create_case_view(self):
        case_id = self.create_case()
        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'crm.helpdesk',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': case_id,
        }

    @api.multi
    def cancel_and_create(self):
        so_pool = self.env['sale.order']
        sobj = so_pool.browse(self._context.get('active_id'))

        # Update name
        order_number = sobj.name
        sobj.write({'name': order_number + '-X'})

        # Cancel Delivery orders
        for picking in sobj.picking_ids:
            if picking.state == 'done':
                raise UserError("Delivery order is already in 'Delivered' state, so can not cancel")

            #wf_service.trg_validate(uid, 'stock.picking', picking.id, 'button_cancel', cr)
            picking_name = picking.name
            picking.write({'name': picking.name + '-X'})

        # Cancel the Order
        # wf_service.trg_validate(uid, 'sale.order', sobj.id, 'cancel', cr)

        # Duplicate Sale Order
        default = {'name': order_number, 'sale_order_payment_id': [], 'picking_ids': []}
        print default,"default"

        new_order_data = so_pool.copy_data(sobj.id,default)


        print new_order_data,"order data"
        new_order_id = so_pool.create(new_order_data)
        print new_order_id,"new order id"
        wf_service = netsvc.LocalService("workflow")
        wf_service.trg_validate('sale.order', new_order_id, 'cancel')

        # Move Payments
        for payment in sobj.sale_order_payment_id:
            payment.write({'sale_order_id': new_order_id})

        return {
            'type': 'ir.actions.act_window',
            'name': 'Sales Order',
            'res_model': 'sale.order',
            'res_id': new_order_id,
            'view_type': 'form',
            'view_mode': 'form, tree',
            'target': 'current',
            'nodestroy': True,
        }


class review_payment(models.TransientModel):
    _name = 'review.payment'

    @api.multi
    def mark_reviewed(self):
        for sobj in self.env['payment.report'].browse(self._context.get('active_ids', [])):
            sobj.payment_id.write({'reviewed': True, 'reviewer_id': self._uid, 'reviewed_date': time.strftime('%Y-%m-%d')})
        return True


class sol_expected_date(models.TransientModel):
    _name = 'sol.expected.date'
    _description = 'Sale Order Line: Set Customer Expected Date'

    @api.multi
    def update_customer_expected_date(self):
        lines = self.env['sale.order.line'].browse(self._context['active_ids'])
        for line in lines:
            if line.product_id.po_expected_date:
                line.write({'cust_expected_date': line.product_id.po_expected_date})
        return True


class sale_order_validate(models.TransientModel):
    _name = 'sale.order.validate'

    msg = fields.Text('Message')
    validation = fields.Char('Validation')
    order_id = fields.Many2one('sale.order', 'Order')
    partner_id = fields.Many2one('res.partner', 'Customer')
    rural_address = fields.Text('Shipping Address')
    license_number = fields.Char('License Number', size=9)
    license_version = fields.Char('Version', size=3)


    @api.multi
    def validate(self):
        sobj = self[0]
        validated = sobj.validation + '_validated'
        context = dict(self._context)
        context.update({validated: True})
        return sobj.order_id.with_context(context).action_confirm_validate()

    @api.multi
    def update_tax(self):
        sobj = self[0]
        for line in sobj.order_id.order_line:
            line.write({'tax_id': [(6, 0, [sobj.order_id.warehouse_id.default_tax_id.id])]})
        return self.validate()

    @api.multi
    def validate_identity(self):
        sobj = self[0]
        if not sobj.license_number or not sobj.license_version:
            raise UserError('Please enter valid license number and version details')
        sobj.order_id.write({'license_number': sobj.license_number, 'license_version': sobj.license_version})
        return self.validate()


class sale_hold_order(models.TransientModel):
    _name = 'sale.hold.order'

    @api.multi
    def _get_held_categ(self):
        held_categ_pool = self.env['sale.order.held.category'].search([])
        res = held_categ_pool.read(['code', 'name'])
        return [(r['code'], r['name']) for r in res]

    mode = fields.Selection([('held','Hold Order'),('exception','Update Exception')], 'Mode')
    order_id = fields.Many2one('sale.order', 'Order')
    order_status = fields.Selection(selection='_get_held_categ', string='Hold Reason')
    future_date = fields.Date('Hold Until')
    exception_reason_ids = fields.Many2many('sale.order.exception', 'rel_sale_hold_order_exception', 'wizard_id', 'exception_id', 'Exception Reason')

    @api.model
    def default_get(self, fields):
        resp = super(sale_hold_order, self).default_get(fields)
        order = self.env['sale.order'].browse(self._context['active_id'])
        resp.update({
            'order_id': order.id,
            'order_status': order.order_status,
            'future_date':  order.future_date,
            'exception_reason_ids': [[6, 0, [e.id for e in order.exception_reason_ids]]],
        })
        return resp

    @api.multi
    def open_dispatch(self):
        sobj = self[0]
        order_vals = {}
        if sobj.mode == 'held':
            if sobj.order_status:
                order_vals['order_status'] = sobj.order_status
                order_vals['order_held'] = True
            if sobj.future_date:
                order_vals['future_date'] = sobj.future_date
        elif sobj.mode == 'exception':
            order_vals['dispatch_exception'] = bool(sobj.exception_reason_ids)
            order_vals['exception_reason_ids'] = [[6, 0, [e.id for e in sobj.exception_reason_ids]]]
        return sobj.order_id.write(order_vals)


class treatme_import(models.TransientModel):
    _name = 'treatme.import'

    csv_file = fields.Binary('CSV File')
    line_ids = fields.One2many('treatme.import.line', 'treatme_id', 'Lines')
    delivery_ids = fields.One2many('treatme.delivery.option', 'treatme_id', 'Do Options')

    @api.onchange
    def onchange_csv(self, csv_file):
        ret = {'value': {}}
        if csv_file:
            buffer = StringIO.StringIO(base64.decodestring(csv_file))
            reader = csv.reader(buffer, delimiter=',')

            delivery_methods = []

            for row in reader:
                if row[0] == 'VoucherNumber':
                    continue
                if row[12] not in delivery_methods:
                    delivery_methods.append(row[12])

            ret['value']['delivery_ids'] = [{'method': x} for x in delivery_methods]

        return ret

    @api.multi
    def do_import(self):
        partner_pool = self.env['res.partner']
        sale_pool = self.env['sale.order']
        line_pool = self.env['sale.order.line']
        payment_pool = self.env['sale.order.payment']

        country_nz_id = self.env['res.country'].search([('name', 'ilike', 'NEW ZEALAND')])[0]

        marketing_method_ttme_id = self.env['sale.order.marketing.method'].search([('code', '=', 'treat_me')])[0]

        sobj = self[0]
        buffer = StringIO.StringIO(base64.decodestring(sobj.csv_file))
        reader = csv.reader(buffer, delimiter=',')

        delivery_carrier_map = {}
        for dc in sobj.delivery_ids:
            if dc.carrier_id.name != 'Pickup' and dc.price <= 0:
                raise UserError('Delivery Price is missing for "%s", its required' % dc.method)
            if dc.method not in delivery_carrier_map:
                delivery_carrier_map[dc.method] = (dc.carrier_id, dc.price)

        order_ids = []
        for row in reader:
            if row[0] == 'VoucherNumber':
                continue

            search_args = []
            if row[2].strip():
                search_args.append(('email', '=', row[2].strip()))
            else:
                search_args.append(('phone', '=', row[10].strip()))

            partner_ids = partner_pool.search(search_args)
            if partner_ids:
                partner_id = partner_ids[0]
            else:
                partner_id = partner_pool.create({'name': row[1], 'email': row[2], 'phone': row[10]})

            order_exist_ids = sale_pool.search([('marketing_method_id', '=', marketing_method_ttme_id), ('client_order_ref', '=', row[0])])
            if order_exist_ids:
                continue

            order_vals = sale_pool.default_get(['pricelist_id'], context=None)

            so_change_vals = sale_pool.onchange_partner_id([], partner_id, False, context=None)['value']
            order_vals.update(so_change_vals)

            order_vals.update({
                'partner_id': partner_id,
                'client_order_ref': row[0],
                'shop_id': 1,
                'marketing_method_id': marketing_method_ttme_id,

                'ship_street': row[5] + (row[6] and (', ' + row[6]) or ''),
                'ship_street2': row[7],
                'ship_city': row[8],
                'ship_zip': row[9],
                'phone': row[10],
                'ship_country_id': country_nz_id,

                'delivery_instructions': row[11],
                'carrier_id': delivery_carrier_map[row[12]][0].id,

                'order_line': []
            })

            carrier = delivery_carrier_map[row[12]][0]
            carrier_price = delivery_carrier_map[row[12]][1]

            for line in sobj.line_ids:
                order_vals['order_line'].append([0, 0, {
                    'name': line.product_id.name,
                    'product_id': line.product_id.id,
                    'price_unit': line.price,
                    'product_uom_qty': line.quantity,
                    'tax_id': [[6, 0, [t.id for t in line.product_id.taxes_id]]],
                }])

            if carrier.name != 'Pickup':
                order_vals['order_line'].append([0, 0, {
                    'name': carrier.product_id.name,
                    'product_id': carrier.product_id.id,
                    'price_unit': carrier_price,
                    'product_uom_qty': 1,
                    'tax_id': [[6, 0, [t.id for t in carrier.product_id.taxes_id]]],
                }])

            order_id = sale_pool.create(order_vals)
            order_ids.append(order_id)

            order = sale_pool.browse(order_id)

            payment_pool.create({
                'sale_order_id': order.id,
                'type': 'payment',
                'method': 'voucher',
                'amount': order.amount_total,
                'reviewed': True,
                'comment': 'Treat Me Voucher: ' + row[0]
            })

        # view_ref = self.env['ir.model.data'].get_object_reference('sale', 'view_order_tree')
        # tree_view_id = view_ref and view_ref[1] or False
        #
        # view_ref = self.env['ir.model.data'].get_object_reference('sale', 'view_order_form')
        # form_view_id = view_ref and view_ref[1] or False
        #
        # view_ref = self.env['ir.model.data'].get_object_reference('sale', 'view_sales_order_filter')
        # search_view_id = view_ref and view_ref[1] or False

        return {
            'type': 'ir.actions.act_window',
            'name': 'TreatMe Orders',
            'res_model': 'sale.order',
            'view_type': 'form',
            'view_mode': 'form,tree',
            # 'views': [(tree_view_id, 'tree'), (form_view_id, 'form')],
            # 'search_view_id': search_view_id,
            'target': 'current',
            'domain': [('id', 'in', order_ids)]
        }


class treatme_import_line(models.TransientModel):
    _name = 'treatme.import.line'
    treatme_id = fields.Many2one('treatme.import', 'Treat Me Import')
    product_id = fields.Many2one('product.product', 'Product')
    quantity = fields.Integer('Quantity', default=1)
    price = fields.Float('Price')


class treatme_delivery_option(models.TransientModel):
    _name = 'treatme.delivery.option'

    treatme_id = fields.Many2one('treatme.import', 'Treat Me Import')
    method = fields.Char('Delivery')
    carrier_id = fields.Many2one('delivery.carrier', 'Carrier')
    price = fields.Float('Price')


class partner_operations(models.TransientModel):
    _name = 'partner.operations'

    @api.model
    def merge_customers(self, ids=None):
        self._cr.execute('''
            select
                max(id) as last_partner_id,
                array_agg(id) as to_update_ids,
                string_agg(name,',') as names,
                string_agg(email,',') as emails,
                string_agg(phone,',') as phones
            from
                res_partner
            WHERE
                email is not null and name is not null and id not in ( select partner_id from res_users) AND
                id not in ( select distinct partner_id from purchase_order ) AND
                customer = True AND supplier = False
            group by
                lower(email) having count(*) > 1
            order by lower(email)
        ''')
        counter = 0
        for rec in self._cr.dictfetchall():
            emails = rec['emails'].split(',')
            if not emails[0]:
                continue
            _logger.info('Merge %s' % emails[0])

            update_partners = rec['to_update_ids']
            update_partners.remove(rec['last_partner_id'])

            self._cr.execute("UPDATE sale_order SET partner_id=%s WHERE partner_id in (%s)" % (rec['last_partner_id'], ",".join(map(str, update_partners))))
            self._cr.execute("UPDATE sale_order SET partner_shipping_id=%s WHERE partner_shipping_id in (%s)" % (rec['last_partner_id'], ",".join(map(str, update_partners))))
            self._cr.execute("UPDATE sale_order SET partner_invoice_id=%s WHERE partner_invoice_id in (%s)" % (rec['last_partner_id'], ",".join(map(str, update_partners))))

            self._cr.execute("UPDATE stock_picking SET partner_id=%s WHERE partner_id in (%s)" % (rec['last_partner_id'], ",".join(map(str, update_partners))))
            self._cr.execute("UPDATE zendesk_ticket SET partner_id=%s WHERE partner_id in (%s)" % (rec['last_partner_id'], ",".join(map(str, update_partners))))

            self._cr.execute("UPDATE account_invoice SET partner_id=%s WHERE partner_id in (%s)" % (rec['last_partner_id'], ",".join(map(str, update_partners))))

            self._cr.execute('DELETE from res_partner WHERE id in (%s)' % ",".join(map(str, update_partners)))

            counter += 1

            if counter == 25:
                self._cr.commit()
                counter = 0

    @api.multi
    def update_first_name_all(self):
        for partner in self.env['res.partner'].search([('first_name', '=', False)]):
            hn = HumanName(partner.name)
            hn.capitalize()
            partner.write({'first_name': hn.first})
        return True


    '''
    <button name="update_first_name_all" string="Update All Partner's First Name" type="object" invisible="0"/>
    <button name="merge_customers" string="Run Merge Customers" type="object" invisible="0"/>
    '''