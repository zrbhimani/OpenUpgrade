# -*- coding: utf-8 -*-

from odoo import tools, api, fields, models, _

from odoo.tools.translate import _
from odoo.tools import html2plaintext
from odoo.exceptions import UserError

import time
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta

import logging

_logger = logging.getLogger('Case')

from common import convert_tz, CASE_REF_LIST, CASE_PRIORITIES, CASE_DEADLINES, decrypt_farmlands_card, SALE_STATES

related_doc_fields = ['ref', 'ref2', 'ref3', 'ref4', 'ref5']


class crm_lead_tag(models.Model):
    _inherit = 'crm.lead.tag'
    _order = 'name'

    @tools.ormcache()
    def _get_held_categ(self):
        return [(cat['code'], cat['name']) for cat in self.env['sale.order.held.category'].search([])]

    name = fields.Char('Name', size=64, required=True, translate=True)
    team_id = fields.Many2one('crm.team', 'Sales Team')
    active = fields.Boolean('Active', default=True)
    export_to_geckoboard = fields.Boolean('Export to Geckoboard')
    product_case = fields.Boolean('Include in Product Issues Case Filter', default=False)

    show_product_opt = fields.Selection([('single', 'Single'), ('multiple', 'Multiple')], 'Product')
    show_return = fields.Boolean('Show Return')
    show_owner = fields.Boolean('Owner')
    show_user = fields.Boolean('Responsible')
    show_section = fields.Boolean('Sales Team')
    show_priority = fields.Boolean('Priority')
    show_description = fields.Boolean('Description')
    show_deadline = fields.Boolean('Deadline')
    show_desi_resolution = fields.Boolean('Desired Customer Resolution')

    owner_id = fields.Many2one('res.users', 'Owner')
    user_id = fields.Many2one('res.users', 'Responsible')
    priority = fields.Selection(CASE_PRIORITIES, 'Priority')
    deadline_days = fields.Selection(CASE_DEADLINES, 'Deadline Days')
    order_status = fields.Selection(_get_held_categ, 'Order Held Category')
    description = fields.Text('Description')


class crm_helpdesk_resolution(models.Model):
    _name = 'crm.helpdesk.resolution'
    _order = 'name'

    name = fields.Char('Name', size=256, required=True)
    section_id = fields.Many2one('crm.team', 'Sales Team')
    active = fields.Boolean('Active', default=True)
    product_case = fields.Boolean('Include in Product Issues Case Filter', default=False)
    show_description = fields.Boolean('Description')
    return_shipping = fields.Boolean('Return Shipping')

    wiz_process_exchange = fields.Boolean('Process Exchange')
    wiz_process_exchange_grp = fields.Many2one('res.groups', string="Group", domain=[('category_id.name', '=', 'Customer Service')])

    @api.multi
    def write(self, vals):
        resp = super(crm_helpdesk_resolution, self).write(vals)
        self.update_case_owner()
        return resp

    @api.multi
    def update_case_owner(self):
        case_pool = self.env['crm.helpdesk']

        all_resolution_ids = self.search([])
        for resolution in all_resolution_ids:
            if resolution.section_id:
                case_ids = case_pool.search([('resolution_id', '=', resolution.id), ('section_id', '!=', resolution.section_id.user_id.id)])
                if case_ids:
                    self._cr.execute("UPDATE crm_helpdesk set section_id = %s WHERE id in (%s)" % (resolution.section_id.id, ",".join(map(str, case_ids))))

        all_issue_ids = self.env['crm.lead.tag'].search([])
        for issue in all_issue_ids:
            if issue.team_id:
                case_ids = case_pool.search([('resolution_id', '=', False), ('categ_id', '=', issue.id), ('team_id', '!=', issue.team_id.id)])
                if case_ids:
                    self._cr.execute("UPDATE crm_helpdesk set team_id = %s WHERE id in (%s)" % (issue.team_id.id, ",".join(map(str, case_ids))))
        return True


class crm_helpdesk(models.Model):
    _name = "crm.helpdesk"
    _description = "Case"
    _order = "id desc"
    _inherit = ['mail.thread', 'base.activity', 'obj.watchers.base']

    id = fields.Integer('ID', readonly=True)
    name = fields.Char('Name', size=128, required=False, default='/')
    active = fields.Boolean('Active', required=False, default=True)
    date_action_last = fields.Datetime('Last Action', readonly=1)
    date_action_next = fields.Datetime('Next Action', readonly=1)
    description = fields.Text('Description')
    create_date = fields.Datetime('Creation Date', readonly=True)
    date_deadline = fields.Date('Deadline')
    user_id = fields.Many2one('res.users', 'Responsible')
    section_id = fields.Many2one('crm.team', 'Sales Team', index=True, default=lambda self: self.env.user.team_id.id, help='Responsible sales team. Define Responsible user and Email account for mail gateway.')

    date_closed = fields.Datetime('Closed', readonly=True)
    email_cc = fields.Text('Watchers Emails', size=252, help="These email addresses will be added to the CC field of all inbound and outbound emails for this record before being sent.")
    email_from = fields.Char('Email', size=128, help="Destination email for email gateway")
    date = fields.Datetime('Date', default=lambda self: fields.Datetime.now())
    planned_revenue = fields.Float('Planned Revenue')
    planned_cost = fields.Float('Planned Costs')
    priority = fields.Selection([('1', 'Highest'), ('2', 'High'), ('3', 'Normal'), ('4', 'Low'), ('5', 'Lowest')], 'Priority', default='3')
    probability = fields.Float('Probability (%)')
    categ_id = fields.Many2one('crm.lead.tag', 'Category', domain="['|',('team_id','=',False),('team_id','=',team_id)]")
    duration = fields.Float('Duration', states={'done': [('readonly', True)]})
    state = fields.Selection([('draft', 'New'), ('cancel', 'Cancelled'), ('open', 'In Progress'), ('resolved', 'Resolved'), ('pending', 'Pending'), ('done', 'Closed')], 'State', size=16, readonly=True, default='draft')

    ref = fields.Reference(CASE_REF_LIST, 'Reference 1', size=128)
    ref2 = fields.Reference(CASE_REF_LIST, 'Reference 2', size=128)
    ref3 = fields.Reference(CASE_REF_LIST, 'Reference 3', size=128)
    ref4 = fields.Reference(CASE_REF_LIST, 'Reference 4', size=128)
    ref5 = fields.Reference(CASE_REF_LIST, 'Reference 5', size=128)

    ref_name = fields.Char(compute='_ref_name', size=64, store=True, string='Reference 1', search='_search_reference')
    ref2_name = fields.Char(compute='_ref_name', size=64, store=True, string='Reference 2')
    ref3_name = fields.Char(compute='_ref_name', size=64, store=True, string='Reference 3')
    ref4_name = fields.Char(compute='_ref_name', size=64, store=True, string='Reference 4')
    ref5_name = fields.Char(compute='_ref_name', size=64, store=True, string='Reference 5')

    resolution_ids = fields.Many2many('crm.helpdesk.resolution', 'rel_crm_helpdesk_resolution', 'case_id', 'resolution_id', 'Resolution')
    resolution_id = fields.Many2one('crm.helpdesk.resolution', compute='_get_resolution', string='Resolution', store=True)
    desi_resolution_ids = fields.Many2many('crm.helpdesk.resolution', 'rel_crm_helpdesk_desi_resolution', 'case_id', 'resolution_id', 'Desired Customer Resolution')

    order_id = fields.Many2one('sale.order', string='Sale Order')
    company_id = fields.Many2one('res.company', related='order_id.company_id', string='Company', store=True, readonly=True)
    partner_id = fields.Many2one('res.partner', related='order_id.partner_id', string='Customer', store=True, readonly=True)

    agent_id = fields.Many2one('res.partner', compute='_sale_order_ref', string="Agent", store=True)
    assembly_contractor_id = fields.Many2one('res.partner', compute='_sale_order_ref', string="Assembly Contractor", store=True)
    supplier_id = fields.Many2one('res.partner', compute='_sale_order_ref', string="Supplier", store=True)
    product_id = fields.Many2one('product.product', compute='_sale_order_ref', string='Product', store=True)
    product_categ_id = fields.Many2one('product.category', compute='_sale_order_ref', string='Product Category', store=True)

    phone = fields.Char(related='order_id.phone', type="char", string='Phone', store=False)
    email = fields.Char(related='partner_id.email', type="char", string='Email', store=False)
    order_state = fields.Selection(SALE_STATES, related='order_id.state', string='SO Status', store=False)
    ship_tt_company_name = fields.Char(related='order_id.ship_tt_company_name', type='char', string='Company', size=64)
    ship_street = fields.Char(related='order_id.ship_street', type='char', string='Street', size=128)
    ship_street2 = fields.Char(related='order_id.ship_street2', type='char', string='Street2', size=128)
    ship_zip = fields.Char(related='order_id.ship_zip', string='Zip', change_default=True, size=24)
    ship_city = fields.Char(related='order_id.ship_city', string='City', size=128)
    ship_state_id = fields.Many2one('res.country.state', related='order_id.ship_state_id', string='State')
    ship_country_id = fields.Many2one('res.country', related='order_id.ship_country_id', string='Country')
    opt_out = fields.Boolean(related='partner_id.opt_out', string="Email Opt-Out")
    owner_id = fields.Many2one('res.users', 'Owner', default=lambda self: self.env.user.id)
    agent_rating = fields.Selection([('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5')], 'Service Agent Rating')
    last_activity_date = fields.Date(compute='_last_activity_date', string="Last Activity Date", store=True)
    description_text = fields.Text(compute='_description_text', string="Description")
    me_and_team = fields.Selection([('my_case', 'My Cases'), ('my_team', 'My Team')], string="Cases")
    date_deadline_formatted = fields.Char(compute='_date_deadline_formatted', string="Deadline")
    pending_date = fields.Date('Pending Date')
    pending_message = fields.Char(compute='_pending_message', string="Pending Message")
    wiz_process_exchange = fields.Boolean(compute='_wiz_options', string="Wizard Process Exchange")
    create_date_hr = fields.Char(compute='_create_date_hr', string="Created")
    create_date = fields.Datetime('Creation Date', readonly=True)
    write_date_hr = fields.Char(compute='_update_date_hr', string='Last Updated', readonly=True)
    write_date = fields.Datetime('Updated Date', readonly=True)

    farmlands_card_enc = fields.Char('Farmlands Card Encoded')
    farmlands_card_exp = fields.Char('Farmlands Card Expiry')
    farmlands_card_num = fields.Char(compute='_farmlands_card_num', string='Farmlands Card')

    sale_return_ids = fields.One2many('sale.order.return', related='order_id.sale_return_ids', string='Return Orders')
    return_note = fields.Char(compute='_return_note', string="Return Note")

    is_owner_team_mate = fields.Boolean(compute='_is_owner_team_mate', string="Is owner team mate")
    image_ids = fields.One2many('ir.attachment', compute='_get_photos', string="Photos")
    case_summary = fields.Text(compute='_case_summary', string="Case Summary")

    channel_id = fields.Many2one('utm.medium', 'Channel', readonly=True)

    @api.multi
    @api.depends('resolution_ids')
    def _get_resolution(self):
        for case in self:
            case.resolution_id = case.resolution_ids and case.resolution_ids[0].id or False

    @api.multi
    def _get_photos(self):
        attach_pool = self.env['ir.attachment']
        for case in self:
            case.image_ids = attach_pool.search(
                [('create_date', '<', '2016-11-25'), ('res_model', '=', 'crm.helpdesk'), ('res_id', '=', case.id), '|', '|', '|', ('name', 'ilike', '.jpg'), ('name', 'ilike', '.png'), ('name', 'ilike', '.jpeg'), ('name', 'ilike', '.gif')])

    @api.multi
    def _case_summary(self):
        for case in self:
            self.case_summary = ''
            if case.product_id and case.categ_id:
                self._cr.execute("""
                        SELECT
                            ch.product_id,
                            chr.name,
                            count(*)
                        from
                            crm_helpdesk ch
                        LEFT JOIN rel_crm_helpdesk_resolution rel on (rel.case_id = ch.id)
                        LEFT JOIN crm_helpdesk_resolution chr on (rel.resolution_id = chr.id)
                        where
                            ch.date > (now() - interval '3 months') AND
                            ch.product_id=%s AND
                            ch.categ_id = %s AND
                            chr.id is not null
                        group by
                            ch.product_id, chr.name
                        order by count(*) desc""" % (case.product_id.id, case.categ_id.id))
                recs = self._cr.dictfetchall()
                total = sum([rec['count'] for rec in recs])
                if total:
                    self.case_summary = '<b>Resolution Stats for %s (last 3 months)</b><br/>' % case.categ_id.name
                for rec in recs:
                    self.case_summary += "%s %d %%<br/>" % (rec['name'] or 'Undefined', round((rec['count'] / float(total)) * 100))

    @api.multi
    def format_date(self, dt):
        today = convert_tz(datetime.utcnow(), 'Pacific/Auckland')
        case_date = convert_tz(datetime.strptime(dt, '%Y-%m-%d %H:%M:%S'), 'Pacific/Auckland')
        date_hr = case_date.strftime('%d %b %Y')
        diff = today - case_date
        diff_seconds = diff.total_seconds()
        if diff_seconds >= 43200:
            if diff.days == 0:
                date_hr += ' (Today)'
            elif diff.days == 1:
                date_hr += ' (1 day ago)'
            elif diff.days < 31:
                date_hr += ' (' + str(diff.days) + ' days ago)'
            elif diff.days > 31 and diff.days < 60:
                date_hr += ' (1 month ago)'
            else:
                date_hr += ' (' + str(int(diff.days / 30)) + ' months ago)'
        else:
            if diff_seconds < 61:
                date_hr += ' (just now)'
            elif diff_seconds < 121:
                date_hr += ' (1 minute ago)'
            elif diff_seconds < 3600:
                date_hr += ' (few minutes ago)'
            elif diff_seconds < 7200:
                date_hr += ' (1 hour ago)'
            elif diff_seconds < 43200:
                date_hr += ' (few hours ago)'

        return date_hr

    @api.multi
    def _create_date_hr(self):
        for case in self:
            self.create_date_hr = self.format_date(case.create_date)

    @api.multi
    def _update_date_hr(self):
        for case in self:
            self.write_date_hr = self.format_date(case.write_date)

    @api.multi
    def _is_owner_team_mate(self):
        user_section_id = self.env.user.team_id
        for case in self:
            if case.section_id and case.section_id.id == user_section_id:
                case.is_owner_team_mate = True

    @api.multi
    def _date_deadline_formatted(self):
        for case in self:
            if case.date_deadline:
                case.date_deadline_formatted = datetime.strptime(case.date_deadline, '%Y-%m-%d').strftime('%a %d %b %Y')

    @api.multi
    def _wiz_options(self):
        gids = [g.id for g in self.env.user.groups_id]
        for case in self:
            if any(r.wiz_process_exchange for r in case.resolution_ids):
                if set(gids) & set([r.wiz_process_exchange_grp.id for r in case.resolution_ids if r.wiz_process_exchange_grp]):
                    case.wiz_process_exchange = True

    @api.multi
    def _pending_message(self):
        for case in self:
            if case.state == 'pending' and case.pending_date:
                case.pending_message = 'Case pending until %s' % datetime.strptime(case.pending_date, '%Y-%m-%d').strftime('%d %b %Y')
            elif case.state == 'pending':
                case.pending_message = 'Case deferred indefinitely'

    @api.multi
    def _farmlands_card_num(self):
        for case in self:
            if case.farmlands_card_enc and case.farmlands_card_exp:
                case.farmlands_card_num = bytes.decode(decrypt_farmlands_card(case.farmlands_card_enc, 'OmpIWmINrqHB4JjFyunLKyZMU34gLmPa', case.farmlands_card_exp.zfill(16)))

    @api.multi
    def _ref_name(self):
        for case in self:
            to_update = {'ref_name': False, 'ref2_name': False, 'ref3_name': False, 'ref4_name': False, 'ref4_name': False}
            if case.ref and self.env[case.ref._name].search([('id', '=', case.ref.id)]):
                to_update['ref_name'] = case.ref.name_get()[0][1]
            if case.ref2 and self.env[case.ref2._name].search([('id', '=', case.ref2.id)]):
                to_update['ref2_name'] = case.ref2.name_get()[0][1]
            if case.ref3 and self.env[case.ref3._name].search([('id', '=', case.ref3.id)]):
                to_update['ref3_name'] = case.ref3.name_get()[0][1]
            if case.ref4 and self.env[case.ref4._name].search([('id', '=', case.ref4.id)]):
                to_update['ref4_name'] = case.ref4.name_get()[0][1]
            if case.ref5 and self.env[case.ref5._name].search([('id', '=', case.ref5.id)]):
                to_update['ref5_name'] = case.ref5.name_get()[0][1]
        case.update(to_update)

    @api.multi
    @api.depends('ref', 'ref2', 'ref3', 'ref4', 'ref5')
    def _sale_order_ref(self):
        for case in self:
            to_update = {'agent_id': False, 'assembly_contractor_id': False, 'supplier_id': False, 'product_id': False, 'product_categ_id': False}

            for fld in ['ref', 'ref2', 'ref3', 'ref4', 'ref5']:
                if eval('case.' + fld) and eval('case.' + fld + '._model') == 'res.partner':
                    partner = self.env['res.partner'].browse(eval('case.' + fld + '.id'))
                    if partner and hasattr(partner, 'is_agent') and partner.is_agent:
                        to_update['agent_id'] = partner.id
                    elif partner and hasattr(partner, 'is_assembly_contractor') and partner.is_assembly_contractor:
                        to_update['assembly_contractor_id'] = partner.id
                    elif partner and hasattr(partner, 'supplier') and partner.supplier:
                        to_update['supplier_id'] = partner.id

                elif eval('case.' + fld) and eval('case.' + fld + '._model') == 'product.product':
                    prod = self.env['product.product'].browse(eval('case.' + fld + '.id'))
                    to_update['product_id'] = prod.id
                    to_update['product_categ_id'] = prod.categ_id.id

            case.update(to_update)

    def _search_reference(self, operator, value):
        return ['|', '|', '|', '|', ('ref_name', 'ilike', value), ('ref2_name', 'ilike', value), ('ref3_name', 'ilike', value), ('ref4_name', 'ilike', value), ('ref5_name', 'ilike', value)]

    @api.multi
    def _return_note(self):
        for case in self:
            return_count = len([r.id for r in case.sale_return_ids if r.state in ['draft', 'processing']])
            if return_count == 1:
                case.return_note = 'There is 1 return relating to this case'
            elif return_count > 1:
                case.return_note = 'There are %s returns relating to this case' % (return_count)

    @api.multi
    def _last_activity_date(self):
        for case in self:
            if case.log_ids:
                case.last_activity_date = case.log_ids[0].date[:10]

    @api.multi
    def _description_text(self):
        for case in self:
            if case.description:
                case.description_text = html2plaintext(case.description.replace('<br/>', '\r\n'))

    @api.multi
    def add_related_document(self, doc_str):
        for case in self:
            for ref in related_doc_fields:
                if not eval('case.' + ref):
                    return case.write({ref: doc_str})
        return False

    @api.one
    def generate_title(self):
        if self.order_id and self.categ_id and self.resolution_id:
            title = self.order_id.name + ' - ' + self.categ_id.name + '/' + self.resolution_id.name
        elif self.categ_id and self.resolution_id:
            title = self.categ_id.name + ' / ' + self.resolution_id.name
        elif self.order_id and self.resolution_id:
            title = self.order_id.name + ' - ' + self.resolution_id.name
        elif self.order_id and self.categ_id:
            title = self.order_id.name + ' - ' + self.categ_id.name
        elif self.order_id:
            title = self.order_id.name
        elif self.categ_id:
            title = self.categ_id.name
        elif self.resolution_id:
            title = self.resolution_id.name
        else:
            title = '/'

        if self.resolution_ids and self.state == 'draft':
            self.case_open()

        vals = {'name': title}
        if self.order_id and self.order_id.company_id.id != self.company_id.id:
            vals['company_id'] = self.order_id.company_id.id

        if self.resolution_id and self.resolution_id.section_id and self.resolution_id.section_id.user_id:
            vals['owner_id'] = self.resolution_id.section_id.user_id.id
        elif self.categ_id and self.categ_id.team_id and self.categ_id.team_id.user_id:
            vals['owner_id'] = self.categ_id.team_id.user_id.id

        return super(models.Model, self).write(vals)


    # Case Actions
    @api.multi
    def case_open(self):
        case = self[0]
        if case.resolution_ids:
            return case.write({'state': 'open'})
        else:
            return case.write({'state': 'draft'})

    @api.one
    def case_cancel(self):
        return self.write({'state': 'cancel'})

    @api.one
    def case_resolved(self):
        return self.write({'state': 'resolved'})

    @api.one
    def case_unresolved(self):
        return self.write({'state': 'open'})

    @api.one
    def case_pending(self):
        return self.write({'state': 'pending'})

    @api.one
    def case_reopen(self):
        resp = self.case_open()
        self.write({'date_closed': False})
        vals = {
            'body': u'<p><br/>Case re-opened by <b>%s</b> <b>%s</b></p><br/>' % (self.env.user.name, convert_tz(datetime.utcnow(), 'Pacific/Auckland').strftime('%d/%m/%Y %I:%M %p %Z')),
            'model': 'crm.helpdesk',
            'res_id': self.id,
            'subtype_id': False,
            'author_id': self.env.user.partner_id.id,
            'type': 'comment'
        }
        self.env['mail.message'].create(vals)
        return resp

    @api.one
    def case_closed(self):
        return self.write({'state': 'done', 'date_closed': fields.datetime.now()})

    @api.multi
    def case_close_with_reason(self):
        return {
            'name': 'Close Case : ' + self[0].name,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'crm.helpdesk.case.close',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    @api.multi
    def case_resolution(self):
        return {
            'name': 'Set Resolution : ' + self[0].name,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'crm.case.resolution',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    @api.model
    def reopen_pending_cases(self):
        pending_cases = self.search([('state', '=', 'pending'), ('pending_date', '<=', time.strftime('%Y-%m-%d'))])
        _logger.info('Reopening Pending Cases: %s' % (len(pending_cases)))
        for case in pending_cases:
            case.case_open()
        return True

    @api.multi
    def create_return(self):
        return {
            'name': 'Return',
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'sale.order.return',
            'type': 'ir.actions.act_window',
        }

    @api.multi
    def crm_helpdesk_open_window(self):
        return {
            'view_type': 'form',
            'view_mode': 'form,tree',
            'res_model': 'crm.helpdesk',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': self[0].id,
        }

    # OnChange
    @api.onchange('user_id')
    def onchange_user_id(self):
        if self.user_id:
            team = self.user_id.team_id
            if team:
                self.section_id = self.user_id.team_id

    # ORM
    @api.model
    def create(self, vals):
        case = super(crm_helpdesk, self).create(vals)
        case.generate_title()
        return case

    @api.multi
    def write(self, vals):
        resp = super(crm_helpdesk, self).write(vals)
        for case in self:
            case.generate_title()
            if 'resolution_ids' in vals:
                if case.state == 'draft':
                    case.case_open()
        return resp

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):

        if 'filter_today' in self._context:
            today = date.today().strftime('%Y-%m-%d')
            start_date = today + ' 00:00:00'
            end_date = today + ' 23:59:59'
            args.append(('create_date', '>', start_date))
            args.append(('create_date', '<', end_date))

        if 'filter_yesterday' in self._context:
            yesterday = (date.today() - timedelta(1)).strftime('%Y-%m-%d')
            start_date = yesterday + ' 00:00:00'
            end_date = yesterday + ' 23:59:59'
            args.append(('create_date', '>', start_date))
            args.append(('create_date', '<', end_date))

        if 'filter_this_week' in self._context:
            date_list = []
            today = date.today()
            while (True):
                date_list.append(today.strftime('%Y-%m-%d'))
                if today.weekday() == 0:
                    break
                today = today - timedelta(1)

            start_date = date_list[-1] + ' 00:00:00'
            end_date = date_list[0] + ' 23:59:59'
            args.append(('create_date', '>', start_date))
            args.append(('create_date', '<', end_date))

        if 'filter_last_week' in self._context:
            date_list = []
            today = date.today()
            this_monday = today - timedelta(days=(today.weekday()))
            while (True):
                this_monday = this_monday - timedelta(1)
                date_list.append(this_monday.strftime('%Y-%m-%d'))
                if this_monday.weekday() == 0:
                    break
            start_date = date_list[-1] + ' 00:00:00'
            end_date = date_list[0] + ' 23:59:59'
            args.append(('create_date', '>', start_date))
            args.append(('create_date', '<', end_date))

        if 'filter_days_7_14' in self._context:
            date_list = []
            date_7 = (date.today() - timedelta(7)).strftime('%Y-%m-%d')
            date_14 = (date.today() - timedelta(14)).strftime('%Y-%m-%d')

            args.append(('state', 'in', ['draft', 'open']))
            args.append(('create_date', '<=', date_7))
            args.append(('create_date', '>=', date_14))

        if 'filter_days_14_old' in self._context:
            date_14 = (date.today() - timedelta(14)).strftime('%Y-%m-%d')
            args.append(('state', 'in', ['draft', 'open']))
            args.append(('create_date', '<=', date_14))

        if 'filter_case_req_actions' in self._context:
            today = convert_tz(datetime.today(), 'Pacific/Auckland')
            today_local_str = today.strftime('%Y-%m-%d')
            self._cr.execute('''select cs.id from crm_helpdesk cs
                            LEFT JOIN res_activity_log lg ON (lg.res_id = cs.id and lg.res_model='crm.helpdesk' and lg.date::date='%s')
                                WHERE
                            cs.state in ('draft','open','pending')
                            AND (cs.owner_id not in (7,15,16) or owner_id is null)
                            AND ( categ_id not in (22) or categ_id is null)
                            AND (cs.date_deadline <='%s' or (cs.date_deadline is null AND lg.id is null)) order by cs.id
                        ''' % (today_local_str, today_local_str))
            resp = self._cr.fetchall()
            args += [('id', 'in', [x[0] for x in resp])]

        if 'my_team' in self._context:
            user_pool = self.env['res.users']
            resolution_pool = self.env['crm.helpdesk.resolution']
            categ_pool = self.env['crm.lead.tag']

            user = self.env.user
            if user.team_id:
                my_teams = [user.team_id.id]
                team = user.team_id
                while team.parent_id:
                    my_teams.append(team.parent_id.id)
                    team = team.parent_id

                resol_ids = resolution_pool.search([('section_id', '=', my_teams)])
                categ_ids = categ_pool.search([('team_id', '=', my_teams)])

                team_user_ids = user_pool.search([('team_id', '=', user.team_id.id), ('id', '!=', user.id)])
                dom = [('owner_id', '!=', self.env.uid), ('user_id', '!=', self.env.uid)]
                dom += ['|', '|', '|', '|', ('owner_id', 'in', team_user_ids), ('user_id', 'in', team_user_ids), ('categ_id', 'in', categ_ids), ('resolution_id', 'in', resol_ids), ('section_id', 'in', my_teams)]
                args += dom
        return super(crm_helpdesk, self).search(args, offset, limit, order, count)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        resp = super(crm_helpdesk, self).read_group(domain, fields, groupby, offset, limit, orderby)
        if groupby:
            if groupby[0] == 'me_and_team':
                user_pool = self.env['res.users']
                resolution_pool = self.env['crm.helpdesk.resolution']
                categ_pool = self.env['crm.lead.tag']

                user = self.env.user
                n_resp = [{'__context': {'group_by': []}, 'me_and_team': 'my_case', '__domain': domain + ['|', ('user_id', '=', self.env.uid), ('owner_id', '=', self.env.uid)],
                           'me_and_team_count': self.search_count(domain + ['|', ('user_id', '=', self.env.uid), ('owner_id', '=', self.env.uid)])}]
                if user.team_id:
                    my_teams = [user.team_id.id]
                    team = user.team_id
                    while team.parent_id:
                        my_teams.append(team.parent_id.id)
                        team = team.parent_id

                    resol_ids = resolution_pool.search([('section_id', '=', my_teams)])
                    categ_ids = categ_pool.search([('team_id', '=', my_teams)])

                    team_user_ids = user_pool.search([('team_id', '=', user.team_id.id), ('id', '!=', user.id)])
                    dom = [('owner_id', '!=', self.env.uid), ('user_id', '!=', self.env.uid)]
                    dom += ['|', '|', '|', '|', ('owner_id', 'in', team_user_ids), ('user_id', 'in', team_user_ids), ('categ_id', 'in', categ_ids), ('resolution_id', 'in', resol_ids), ('section_id', 'in', my_teams)]
                    dom += filter(lambda x: x[0] not in ['owner_id', 'user_id'], domain)

                    if dom[-1] == '|':
                        dom = dom[:-1]
                    n_resp += [{'__context': {'group_by': []}, 'me_and_team': 'my_team', '__domain': dom, 'me_and_team_count': self.search_count(dom)}]
                resp = n_resp
        return resp


class crm_helpdesk_agent(models.Model):
    _name = "crm.helpdesk.agent"
    _inherit = "crm.helpdesk"
    _table = "crm_helpdesk"
    _description = 'Cases for Service Agent'


class crm_helpdesk_assembly_contractor(models.Model):
    _name = "crm.helpdesk.assembly.contractor"
    _inherit = "crm.helpdesk"
    _table = "crm_helpdesk"
    _description = 'Cases for Assembly Contractor'
