# -*- coding: utf-8 -*-
from openerp import tools, api, fields, models, _
from datetime import datetime
from common import purchase_states, SALE_STATES_DICT, CASE_STATES_DICT, DELIVERY_STATES_DICT, product_status, convert_tz, utc_to_nz

track = {
    'purchase.order': {
        'state': ['selection', 'Status', purchase_states],
        'landing_date': ['date', 'Landing Date'],
        'date_planned': ['date', 'Expected Date'],
    },
    'sale.order': {
        'state': ['selection', 'Status', SALE_STATES_DICT],
        'user_id': ['many2one', 'Salesperson', 'res.users'],
        'partner_id': ['many2one', 'Customer', 'res.partner'],
        'phone': ['char', 'Phone'],
        'date_order': ['date', 'Order Date'],
        'order_status': ['char', 'Held Category'],
        'carrier_id': ['many2one', 'Delivery Method', 'delivery.carrier'],
        'ship_tt_company_name': ['char', 'Company'],
        'ship_street': ['char', 'Street'],
        'ship_street2': ['char', 'Street2'],
        'ship_city': ['char', 'City'],
        'ship_zip': ['char', 'Zip'],
        'ship_state_id': ['many2one', 'State', 'res.country.state'],
        'ship_country_id': ['many2one', 'Country', 'res.country'],
        'delivery_instructions': ['char', 'Delivery Instructions'],
        'signature_opt_out': ['boolean', 'Signature Not Req.'],
    },
    'crm.helpdesk': {
        'state': ['selection', 'Status', CASE_STATES_DICT],
        'user_id': ['many2one', 'Responsible', 'res.users'],
        'owner_id': ['many2one', 'Owner', 'res.users'],
        'date_deadline': ['date', 'Date Deadline'],
        'categ_id': ['many2one', 'Issue', 'crm.lead.tag'],
        'resolution_id': ['many2one', 'Resolution', 'crm.helpdesk.resolution'],
    },
    'product.product': {
        'state': ['selection', 'Status', product_status],
        'description': ['char', 'Description'],
        'name': ['char', 'Name'],
        'default_code': ['char', 'SKU'],
        'list_price': ['float', 'Standard Price'],
        'special_price': ['float', 'Special Price'],
        'special_from_date': ['datetime', 'Special From Date'],
        'special_to_date': ['datetime', 'Special To Date'],
        'active': ['boolean', 'Active'],
    },
    'stock.picking': {
        'carrier_id': ['many2one', 'Carrier', 'delivery.carrier'],
        'carrier_tracking_ref': ['char', 'Consignment Number'],
    }
}

track2 = {
    # This list will be processed inside chatter message tracking, fields should be set to track_visibility=onchange
    # Function field doesn't reflect in write method, so here is the best place to track it
    'stock.picking': {
        'state': ['selection', 'Status', DELIVERY_STATES_DICT],
    }
}


class res_activity_log(models.Model):
    _name = 'res.activity.log'
    _log_access = False
    _order = 'res_id, date desc, id desc'

    user_id = fields.Many2one('res.users', 'User', default=lambda self: self.env.user.id)
    date = fields.Datetime('DateTime', default=lambda self: datetime.now())
    res_id = fields.Integer('Resource ID', index=True)
    res_model = fields.Char('Model', index=True)
    activity = fields.Char('Change in', size=256)
    value_before = fields.Text('From')
    value_after = fields.Text('To')
    move_id = fields.Integer('Move Ref.')

class base_activity(models.AbstractModel):
    _name = 'base.activity'
    log_ids = fields.One2many('res.activity.log', 'res_id', domain=lambda self: [('res_model', '=', self._name)], auto_join=True)

    @api.multi
    def write(self, vals):
        for obj in self:
            if self._name in track:
                for k, v in track[self._name].items():
                    if k in vals:
                        if v[0] == 'selection':
                            val_before = obj[k] and v[2][obj[k]] or False
                            val_after = vals[k] and v[2][vals[k]] or False
                        elif v[0] == 'date':
                            val_before = obj[k] and datetime.strptime(obj[k], '%Y-%m-%d').strftime('%d/%m/%Y') or False
                            val_after = vals[k] and datetime.strptime(vals[k], '%Y-%m-%d').strftime('%d/%m/%Y') or False
                        elif v[0] == 'datetime':
                            val_before = obj[k] and utc_to_nz(obj[k], '%d/%m/%Y  %H:%M:%S') or False
                            val_after = vals[k] and utc_to_nz(vals[k], '%d/%m/%Y  %H:%M:%S') or False
                        elif v[0] == 'many2one':
                            val_before = obj[k] and obj[k].name or ''
                            val_after = vals[k] and self.env[v[2]].browse(vals[k]).name or False
                        elif v[0] == 'char':
                            val_before = obj[k]
                            val_after = vals[k]
                        elif v[0] == 'float':
                            val_before = '%.2f' %obj[k]
                            val_after = '%.2f' %vals[k]
                        elif v[0] == 'boolean':
                            val_before = str(obj[k])
                            val_after = str(vals[k])
                        if val_before != val_after:
                            self.env['res.activity.log'].create({'res_model': self._name, 'res_id': obj.id, 'activity': v[1], 'value_before': val_before, 'value_after': val_after})
        return super(base_activity, self).write(vals)


class mail_thread(models.AbstractModel):
    _inherit = 'mail.thread'

    @api.multi
    def _message_track(self, tracked_fields, initial):
        resp = super(mail_thread, self)._message_track(tracked_fields, initial)
        if self._name in track2:
            for rec in resp[1]:
                if rec[2]['field'] in track2[self._name]:
                    self.env['res.activity.log'].create({'res_model': self._name, 'res_id': self.id, 'activity': track2[self._name][rec[2]['field']][1], 'value_before': rec[2]['old_value_char'], 'value_after': rec[2]['new_value_char']})
        return resp
