# -*- encoding: utf-8 -*-

from odoo import tools, api, fields, models, _
import time
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from odoo.exceptions import except_orm, ValidationError
from odoo.exceptions import UserError

from odoo.tools.safe_eval import safe_eval
import json

from odoo.addons.tradetested.models.common import convert_tz
import pytz
import logging

_logger = logging.getLogger(__name__)

AVAILABLE_PRIORITIES = [
    ('5', 'Lowest'),
    ('4', 'Low'),
    ('3', 'Normal'),
    ('2', 'High'),
    ('1', 'Highest')
]


class service_agent(models.TransientModel):
    _name = 'service.agent'

    partner_id = fields.Many2one('res.partner', 'Service Agent')

    @api.multi
    def do_next(self):
        case_id = self._context.get('active_id')
        sobj = self[0]
        case_pool = self.env['crm.helpdesk'].write({'ref4': 'res.partner,' + str(sobj.partner_id.id)})
        return self.open_message_wizard()

    @api.multi  # TODO check mail
    def open_message_wizard(self):
        case_id = self._context.get('active_id')
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data.get_object_reference('tradetested', 'email_template_service_agent')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data.get_object_reference('mail', 'email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = dict(self._context)
        ctx.update({
            'default_model': 'crm.helpdesk.agent',
            'default_res_id': case_id,
            'default_use_template': bool(template_id),
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
        })
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
        }


class assembly_contractor(models.TransientModel):
    _name = 'assembly.contractor'

    partner_id = fields.Many2one('res.partner', 'Assembly Contractor', domain=[('is_assembly_contract', '=', True)])

    @api.multi
    def do_next(self):
        case_id = self._context.get('active_id')
        sobj = self[0]
        case_pool = self.env['crm.helpdesk'].write({'ref5': 'res.partner,' + str(sobj.partner_id.id)})
        return self.open_message_wizard()

    @api.multi  # TODO check mail message
    def open_message_wizard(self):
        case_id = self._context.get('active_id')
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data.get_object_reference('tradetested', 'email_template_assembly_contractor')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data.get_object_reference('mail', 'email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = dict(self._context)
        ctx.update({
            'default_model': 'crm.helpdesk.assembly.contractor',
            'default_res_id': case_id,
            'default_use_template': bool(template_id),
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
        })
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
        }


class crm_case_resolution(models.TransientModel):
    _name = 'crm.case.resolution'

    case_id = fields.Many2one('crm.helpdesk', 'Case')
    resolution_id = fields.Many2one('crm.helpdesk.resolution', 'Resolution')
    description = fields.Text('Description')

    @api.model
    def default_get(self, fields):
        resp = super(crm_case_resolution, self).default_get(fields)
        case = self.env['crm.helpdesk'].browse(self._context['active_id'])
        resp['case_id'] = case.id
        resp['description'] = case.description
        return resp

    @api.multi
    def case_open(self):
        sobj = self[0]
        case_vals = {'description': sobj.description}
        if sobj.resolution_id:
            case_vals['resolution_ids'] = [[4, sobj.resolution_id.id]]
        sobj.case_id.write(case_vals)
        if sobj.case_id.state != 'open':
            self.env['crm.helpdesk'].case_open([sobj.case_id.id])
        return True


class crm_case_pending(models.TransientModel):
    _name = 'crm.case.pending'

    pending_options = fields.Selection([('tomorrow', 'Tomorrow'), ('next_week', 'Next Week'), ('pick_date', 'Pick Date')], 'Set case as pending until')
    pending_date = fields.Date('Pending Date')

    @api.multi
    def set_pending(self):
        case_pool = self.env['crm.helpdesk']
        sobj = self[0]
        case = case_pool.browse(self._context['active_id'])
        case.case_pending()
        today = datetime.strptime(time.strftime('%Y-%m-%d %H:%M:%S'), '%Y-%m-%d %H:%M:%S')
        today = convert_tz(today, 'Pacific/Auckland')
        if sobj.pending_options == 'tomorrow':
            pending_date = (today + relativedelta(days=1)).strftime('%Y-%m-%d')
            case.write({'pending_date': pending_date, 'date_deadline': pending_date})
        elif sobj.pending_options == 'next_week':
            pending_date = (today + timedelta(days=-today.weekday(), weeks=1)).strftime('%Y-%m-%d')
            case.write({'pending_date': pending_date, 'date_deadline': pending_date})
        elif sobj.pending_date:
            case.write({'pending_date': sobj.pending_date, 'date_deadline': sobj.pending_date})
        else:
            case.write({'pending_date': False, 'date_deadline': False})
        return True


class crm_helpdesk_case_close(models.TransientModel):
    _name = 'crm.helpdesk.case.close'

    reason = fields.Text('Reason for Close')

    @api.multi
    def case_close(self):
        sobj = self[0]
        case_pool = self.env['crm.helpdesk']
        case = case_pool.browse(self._context['active_id'])
        case.case_closed()
        user = self.env.user
        today = datetime.today()
        tz_name = user.tz

        if tz_name:
            utc = pytz.timezone('UTC')
            context_tz = pytz.timezone(tz_name)
            local_timestamp = utc.localize(today, is_dst=False)
            user_datetime = local_timestamp.astimezone(context_tz)
        else:
            user_datetime = datetime.now()

        vals = {
            'body': u'<p><br/>Case Closed by <b>%s</b> %s<br/>Reason: %s<br/></p>&nbsp;<br/>' % (user.name, user_datetime.strftime('%d/%m/%Y %I:%M %p %Z'), sobj.reason),
            'model': 'crm.helpdesk',
            'res_id': case.id,
            'subtype_id': False,
            'author_id': user.partner_id.id,
            'type': 'comment', }

        self.env['mail.message'].create(vals)


class crm_case_create(models.TransientModel):
    @api.multi
    def _get_order_products(self):
        if self._context.get('sale_order_id'):
            return [(line.product_id.id, line.product_id.name) for line in self.env['sale.order'].browse(self._context['sale_order_id']).order_line]
        return []

    _name = 'crm.case.create'

    categ_id = fields.Many2one('crm.lead.tag', 'Issue')
    resolution_id = fields.Many2one('crm.helpdesk.resolution', 'Resolution')

    order_id = fields.Many2one('sale.order', 'Sale Order')

    # show_product = fields.Boolean('Product')
    show_product_opt = fields.Selection([('single', 'Single'), ('multiple', 'Multiple')], 'Product')
    show_return = fields.Boolean('Return Shipment')
    show_owner = fields.Boolean('Owner')
    show_user = fields.Boolean('Responsible')
    show_section = fields.Boolean('Sales Team')
    show_priority = fields.Boolean('Priority')
    show_description = fields.Boolean('Description')
    show_deadline = fields.Boolean('Deadline')
    show_desi_resolution = fields.Boolean('Desired Customer Resolution')

    order_product_ids = fields.Many2many('product.product', 'create_case_products_rel', 'wizard_id', 'product_id', 'Order Products')
    product_id = fields.Many2one('product.product', 'Product')
    line_ids = fields.One2many('crm.case.create.line', 'case_id', 'Products')
    return_shipment = fields.Boolean('Create Return Shipment')

    owner_id = fields.Many2one('res.users', 'Owner')
    user_id = fields.Many2one('res.users', 'Responsible')
    section_id = fields.Many2one('crm.team', 'Sales Team')
    # section_id = fields.Many2one('crm.case.section', 'Sales Team')
    priority = fields.Selection(AVAILABLE_PRIORITIES, 'Priority')
    description = fields.Text('Description')
    date_deadline = fields.Date('Deadline')
    desi_resolution_ids = fields.Many2many('crm.helpdesk.resolution', 'rel_crm_case_create_desi_resolution', 'wiz_id', 'resolution_id', 'Desired Customer Resolution')

    @api.multi
    def create_returns(self):
        """
         Creates return picking.
         @param self: The object pointer.
         @param cr: A database cursor
         @param uid: ID of the user currently logged in
         @param ids: List of ids selected
         @param context: A standard dictionary
         @return: A dictionary which of fields with values.
        """
        move_obj = self.env['stock.move']
        pick_obj = self.env['stock.picking']
        uom_obj = self.env['product.uom']
        data_obj = self.env['stock.return.picking.memory']
        act_obj = self.env['ir.actions.act_window']
        model_obj = self.env['ir.model.data']
        wf_service = netsvc.LocalService("workflow")

        pick = pick_obj.browse(self._context['picking_id'])
        data = self.read[0]

        set_invoice_state_to_none = True
        returned_lines = 0

        seq_obj_name = 'stock.picking'
        new_type = 'internal'
        if pick.type == 'out':
            new_type = 'in'
            seq_obj_name = 'stock.picking'
        elif pick.type == 'in':
            new_type = 'out'
            seq_obj_name = 'stock.picking'
        new_pick_name = self.env['ir.sequence'].get(seq_obj_name)
        new_picking = pick_obj.copy(pick.id, {
            'name': _('%s-%s-return') % (new_pick_name, pick.name),
            'move_lines': [],
            'state': 'draft',
            'type': new_type,
            'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            'invoice_state': 'none',
            'log_ids': [],
        })

        for mov_id in self._context.get('move_ids', []):
            new_qty = 1

            move = move_obj.browse(mov_id)
            new_location = move.location_dest_id.id
            returned_qty = move.product_qty

            for rec in move.move_history_ids2:
                returned_qty -= rec.product_qty

            if returned_qty != new_qty:
                set_invoice_state_to_none = False

            if new_qty:
                returned_lines += 1
                new_move = move_obj.copy(move.id, {
                    'product_qty': new_qty,
                    'product_uos_qty': uom_obj._compute_qty(move.product_uom.id, new_qty, move.product_uos.id),
                    'picking_id': new_picking,
                    'state': 'draft',
                    'location_id': new_location,
                    'location_dest_id': move.location_id.id,
                    'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                })
                move.write({'move_history_ids2': [(4, new_move)]})

            if not returned_lines:
                raise except_orm("Please specify at least one non-zero quantity.")

        if set_invoice_state_to_none:
            pick.write({'invoice_state': 'none'})
        wf_service.trg_validate('stock.picking', new_picking, 'button_confirm')
        pick_obj.force_assign([new_picking])
        # Update view id in context, lp:702939

        return new_picking

    @api.model
    def default_get(self, fields):
        resp = super(crm_case_create, self).default_get(fields)

        resp['order_product_ids'] = []
        resp['line_ids'] = []
        resp['order_id'] = self.env.context.get('active_id', False)

        order = self.env['sale.order'].browse(resp['order_id'])
        for line in order.order_line:
            if line.product_id and line.product_id.type != 'service':
                resp['order_product_ids'].append(line.product_id.id)
                resp['line_ids'].append([0, 0, {'product_id': line.product_id.id}])
        return resp

    @api.onchange('categ_id')
    def onchange_categ_id(self):
        ret = {'value': {}}
        if self.categ_id:
            issue = self.categ_id
            ret['value']['show_product_opt'] = issue.show_product_opt
            ret['value']['show_return'] = issue.show_return
            ret['value']['return_shipment'] = issue.show_return

            ret['value']['show_owner'] = issue.show_owner
            ret['value']['show_user'] = issue.show_user
            ret['value']['show_section'] = issue.show_section
            ret['value']['show_priority'] = issue.show_priority
            ret['value']['show_description'] = issue.show_description
            ret['value']['show_deadline'] = issue.show_deadline

            ret['value']['description'] = issue.description

            ret['value']['owner_id'] = issue.owner_id and issue.owner_id.id or False
            ret['value']['user_id'] = issue.user_id and issue.user_id.id or False
            ret['value']['team_id'] = issue.team_id and issue.team_id.id or False
            ret['value']['priority'] = issue.priority
            ret['value']['show_desi_resolution'] = issue.show_desi_resolution or False

        return ret

    @api.onchange('resolution_id')
    def onchange_resolution_id(self):
        ret = {'value': {}}
        if self.resolution_id:
            resolution = self.resolution_id
            ret['value']['show_description'] = resolution.show_description

        return ret

    @api.onchange('product_id', 'description')
    def onchange_product_id(self):
        ret = {'value': {}}
        if self.product_id:
            product = self.product_id
            product_name = (product.default_code and ('[' + product.default_code + '] ') or '') + product.name
            if self.description:
                ret['value']['description'] = self.description.replace('[PRODUCT]', product_name)
        return ret

    @api.multi
    def eval_user_input(self):
        sobj = self[0]

        for user_run in sobj.template_id.runtime_ids:
            if sobj.cur_seq == user_run.sequence and sobj.type == user_run.type:
                ctx = {
                    'self': self,
                    'cr': self._cr,
                    'uid': self.env.uid,
                    'time': time, 'datetime': datetime,
                    'user': self.env.user,
                    'sale_order': sobj.sale_order_id,
                    'data': json.loads(sobj.data),
                    'sobj': sobj
                }

                if sobj.type == 'multi_prod':
                    ctx['product_ids'] = [int(l.product_id) for l in sobj.line_ids]

                elif sobj.type == 'single_prod':
                    ctx['product_id'] = int(sobj.product_id)

                elif sobj.type == 'text':
                    ctx['description'] = sobj.description

                if user_run.code:
                    safe_eval(user_run.code.strip(), ctx, mode="exec", nocopy=True)
                    sobj.write({'data': json.dumps(ctx['data'])})

    @api.multi
    def create_case_view(self):
        case_id = self.create_case
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

        sobj = self[0]

        if sobj.type:
            self.eval_user_input()

        sobj.refresh()

        data = json.loads(sobj.data)

        ctx = {'self': self,
               'cr': self._cr,
               'uid': self.env.uid,
               'time': time,
               'datetime': datetime,
               'user': self.env.user,
               'sale_order': sobj.sale_order_id,
               'data': data,
               }

        case_id = False
        template = sobj.template_id
        if template.custom_code:
            safe_eval(template.custom_code.strip(), ctx, mode="exec", nocopy=True)
            if 'case_id' in ctx:
                case_id = ctx['case_id']

        return case_id

    @api.multi
    def open_case(self):
        case_pool = self.env['crm.helpdesk']
        sobj = self[0]

        description = sobj.description

        if description:
            address = "%s<b/>%s<b/>%s, %s" % (sobj.order_id.ship_street, sobj.order_id.ship_street2, sobj.order_id.ship_city, sobj.order_id.ship_zip)
            description = description.replace('[CUSTOMER_NAME]', sobj.order_id.partner_id.name)
            description = description.replace('[ADDRESS]', address)
            description = description.replace('[PHONE]', (sobj.order_id.phone or ''))

        case_vals = {
            'categ_id': sobj.categ_id.id,
            'order_id': sobj.order_id.id,
            'description': description,
        }

        if sobj.desi_resolution_ids:
            case_vals['desi_resolution_ids'] = [[6, 0, [res.id for res in sobj.desi_resolution_ids]]]

        if sobj.resolution_id:
            case_vals['resolution_ids'] = [[6, 0, [sobj.resolution_id.id]]]

        if sobj.categ_id.order_status:
            sobj.order_id.write({'order_status': sobj.categ_id.order_status, 'order_held': True})

        if sobj.categ_id.owner_id:
            case_vals['owner_id'] = sobj.categ_id.owner_id.id
        else:
            case_vals['owner_id'] = sobj.env.user.id

        if sobj.categ_id.user_id:
            case_vals['user_id'] = sobj.categ_id.user_id.id
        else:
            case_vals['user_id'] = sobj.env.user.id

        if sobj.categ_id.team_id:
            case_vals['team_id'] = sobj.categ_id.team_id.id
        else:
            case_vals['team_id'] = sobj.env.user.id

        if sobj.categ_id.priority:
            case_vals['priority'] = sobj.categ_id.priority

        if sobj.categ_id.show_deadline and sobj.date_deadline:
            case_vals['date_deadline'] = sobj.date_deadline

        elif sobj.categ_id.deadline_days:
            now_local = convert_tz(datetime.today(), 'Pacific/Auckland')
            deadline_days = int(sobj.categ_id.deadline_days)
            case_vals['date_deadline'] = now_local + timedelta(days=deadline_days)

        case = self.env['crm.helpdesk'].create(case_vals)

        product_ids = []

        if sobj.categ_id.show_product_opt == 'single' and sobj.product_id:
            self.env['crm.helpdesk'].add_related_document('product.product,%s' % sobj.product_id.id)
            product_ids.append(sobj.product_id.id)
        elif sobj.categ_id.show_product_opt == 'multiple' and sobj.line_ids:
            for line in sobj.line_ids:
                if line.product_id.id not in product_ids:
                    self.env['crm.helpdesk'].add_related_document('product.product,%s' % line.product_id.id)
                    product_ids.append(line.product_id.id)
                product_ids = list(set(product_ids))

        move_ids = []
        if sobj.resolution_id.return_shipping or sobj.return_shipment:
            if product_ids:
                for delivery in sobj.order_id.picking_ids:
                    if delivery.type != 'out':
                        continue
                    for move in delivery.move_lines:
                        if move.product_id.id in product_ids:
                            move_ids.append(move.id)

        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'crm.helpdesk',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': case.id,
        }

    @api.model
    def fields_view_get(self, view_id=None, view_type=False, toolbar=False, submenu=False):
        resp = super(crm_case_create, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if 'line_ids' in resp['fields']:
            if self._context.get('active_id') and self._context.get('active_model') == 'sale.order':
                order = self.env['sale.order'].browse(self._context['active_id'])
                prod_ids = []
                prod_sel = []
                for line in order.order_line:
                    if line.product_id and line.product_id.type != 'service':
                        prod_ids.append(line.product_id.id)
                        prod_sel.append((line.product_id.id, line.product_id.name))

                resp['fields']['line_ids']['views']['tree']['fields']['product_id']['domain'] = [('id', '<', prod_ids)]
                resp['fields']['line_ids']['views']['tree']['fields']['product_id']['selection'] = prod_sel
        return resp


class crm_case_create_line(models.TransientModel):
    _name = 'crm.case.create.line'

    @api.multi
    def _get_order_products(self):
        so_id = self._context.get('active_id')
        ret_val = []
        if so_id:
            so_obj = self.emv['sale.order'].browse(so_id)
            for line in so_obj.order_line:
                if line.product_id.type != 'service':
                    ret_val.append((line.product_id.id, line.product_id.name))
        return ret_val

    product_id = fields.Many2one('product.product', 'Products')
    case_id = fields.Many2one('crm.case.create', 'Case')
    move_id = fields.Many2one('stock.move', "Move")


class create_case_line(models.TransientModel):
    @api.multi
    def _get_order_products(self):
        so_id = self._context.get('active_id')
        ret_val = []
        if so_id:
            so_obj = self.env['sale.order'].browse(so_id)
            for line in so_obj.order_line:
                ret_val.append((line.product_id.id, line.product_id.name))
        return ret_val

    _name = 'create.case.line'

    product_id = fields.Selection(_get_order_products, 'Products')
    case_id = fields.Many2one('create.case', 'Case')


class create_case(models.TransientModel):
    _name = 'create.case'

    case_line_ids = fields.One2many('create.case.line', 'case_id', 'Cases')

    @api.multi
    def create_case_view(self):
        case_id = self.create_case()
        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'crm.helpdesk',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': case_id.id,
        }

    @api.multi
    def create_case(self):
        order_id = self._context.get('active_id')
        sobj = self[0]
        case_pool = self.env['crm.helpdesk']
        for case_line in sobj.case_line_ids:
            vals = {
                'name': ' ',
                'order_id': order_id,
                'ref2': case_line.product_id and ('product.product,' + str(case_line.product_id)) or False
            }

            case_id = case_pool.create(vals)
            case_id.write({'state': 'draft'})

        if not sobj.case_line_ids:
            case_id = case_pool.create({'name': ' ', 'order_id': order_id})

        return case_id
