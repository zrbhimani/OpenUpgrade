# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
import requests
from datetime import datetime
from dateutil import parser
import time
import pytz
import logging
from odoo.exceptions import except_orm, ValidationError

_logger = logging.getLogger('Zendesk')

def convert_datetime(str_date):
    if str_date:
        dt = parser.parse(str_date)
        utc = pytz.timezone('UTC')
        return dt.astimezone(utc).strftime('%Y-%m-%d %H:%M:%S')
    return False

READONLY_STATES = {'Connected': [('readonly', True)]}


class zendesk_config(models.Model):
    _name = 'zendesk.config'

    @api.multi
    def _get_statistics(self):
        self._cr.execute('SELECT max(created_at) from zendesk_ticket')
        resp = self._cr.fetchone()
        self._cr.execute('SELECT count(*) from zendesk_ticket')
        resp_count = self._cr.fetchone()
        for config in self:
            config.last_date = resp and resp[0]
            config.ticket_counts = resp_count and resp_count[0]

    url = fields.Char('URL', size=256, states={'Connected': [('readonly', True)]}, default='https://YOUR-URL.zendesk.com')
    name = fields.Char('Email / User', size=256, states={'Connected': [('readonly', True)]})
    auth_method = fields.Selection([('basic', 'Basic'), ('token', 'Authentication Token')], states={'Connected': [('readonly', True)]}, string="Authentication Method")
    password = fields.Char('Password', size=256, states={'Connected': [('readonly', True)]})
    token = fields.Char('Token', size=512, states={'Connected': [('readonly', True)]})
    state = fields.Selection([('New', 'New'), ('Connected', 'Connected'), ('Error', 'Error')], string="Status", default='New')
    start_sync = fields.Date('Import Tickets from This date', states={'Connected': [('readonly', True)]})
    last_date = fields.Datetime(compute=_get_statistics, string="Last Ticket", multi='stats')
    ticket_counts = fields.Integer(compute=_get_statistics, string="Counts", multi='stats')

    @api.multi
    def reset_to_draft(self):
        return self.write({'state': 'New'})

    @api.multi
    def reset_to_draft(self):
        return self.write({'state': 'New'})

    @api.multi
    def request(self, req_url=False):
        if self.auth_method == 'basic':
            response = requests.get(self.url + req_url, auth=(self.name, self.password))
            if response.status_code != 200:
                _logger.error('Error (%s) : %s' % (response.status_code, response.text))

        elif self.auth_method == 'token':
            response = requests.get(self.url + req_url, auth=(self.name + "/token", self.token))
            if response.status_code != 200:
                _logger.error('Error (%s) : %s' % (response.status_code, response.text))

        return response.json()

    @api.multi
    def test_connection(self):
        data = self.request('/api/v2/users/me.json')
        if data.get('user', {}).get('verified') == True:
            self.write({'state': 'Connected'})
        else:
            self.write({'state': 'Error'})
        return True

    @api.model
    def import_tickets_cron(self):
        objs = self.search([])
        objs.import_tickets()

    @api.multi
    def import_tickets(self):
        self._cr.execute("SELECT max(gen_timestamp) from zendesk_ticket")
        resp = self._cr.fetchall()
        if resp and resp[0][0]:
            start_epoch = resp[0][0] + 1
        else:
            sobj = self[0]
            start_epoch = datetime.strptime(sobj.start_sync, '%Y-%m-%d').strftime('%s')

        int_mins = (int(time.strftime('%s')) - int(start_epoch)) / 60
        if int_mins < 6:
            return
        _logger.info('Importing Zendesk Tickets from %s' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(start_epoch))))
        try:
            tickets = self.request('/api/v2/exports/tickets.json?start_time=' + str(start_epoch))
            if 'results' not in tickets:
                return
        except Exception, e:
            _logger.error(e)
            return

        for ticket in tickets['results']:
            ticket_vals = {
                'zendesk_id': ticket['id'],
                'name': ticket['subject'],
                'domain': ticket['domain'],
                'channel': ticket['via'],
                'type': ticket['ticket_type'],
                'requester': ticket['req_name'],
                'url': ticket['url'],
                'state': ticket['status'],
                'created_at': convert_datetime(ticket['created_at']),
                'updated_at': convert_datetime(ticket['updated_at']),
                'assigned_at': convert_datetime(ticket['assigned_at']),
                'solved_at': convert_datetime(ticket['solved_at']),
                'gen_timestamp': ticket['generated_timestamp'],
            }
            if ticket['current_tags']:
                tag_ids = []
                for tag in ticket['current_tags'].split(','):
                    tag = tag.strip()
                    tag_exist = self.env['zendesk.tag'].search([('name', '=', tag)])
                    if not tag_exist:
                        tag = self.env['zendesk.tag'].create({'name': tag})
                    else:
                        tag = tag_exist[0]
                    tag_ids.append(tag.id)

                ticket_vals['tag_ids'] = [[6, 0, tag_ids]]

            ticket_exists = self.env['zendesk.ticket'].search([('zendesk_id', '=', ticket_vals['zendesk_id'])])
            if ticket_exists:
                ticket = ticket_exists[0]
                ticket.write(ticket_vals)
            else:
                ticket = self.env['zendesk.ticket'].create(ticket_vals)
            # IMPORT LOGS
            self.import_ticket_log(ticket_vals['zendesk_id'], ticket.id)
            self._cr.commit()

        self._cr.commit()
        if 'next_page' in tickets:
            # time.sleep(60)
            # self.import_tickets(cr, uid, ids, context)
            # Do nothing, it'll import in next call
            pass
        return True

    @api.multi
    def import_ticket_log(self, zendesk_id=False, ticket_id=False):
        comments = self.request('/api/v2/tickets/' + str(zendesk_id) + '/comments.json')
        for comment in comments.get('comments', []):
            logs = self.env['zendesk.log'].search([('zendesk_id', '=', comment['id'])])
            if logs:
                log = logs[0]
            else:
                vals = {'comment': comment['body'], 'ticket_id': ticket_id, 'zendesk_id': comment['id'], 'date': comment['created_at'], 'public': comment['public'],}
                if comment['via']['source'].get('from'):
                    vals['from_name'] = comment['via']['source']['from'].get('name')
                    vals['from_email'] = comment['via']['source']['from'].get('address')

                log = self.env['zendesk.log'].create(vals)

            for attach in comment['attachments']:
                attachs = self.env['zendesk.log.attachment'].search([('zendesk_id', '=', attach['id'])])
                if attachs:
                    attach = attachs
                else:
                    attach = self.env['zendesk.log.attachment']
        return True


class zendesk_ticket(models.Model):
    _name = 'zendesk.ticket'

    zendesk_id = fields.Char('Zendesk-ID')
    name = fields.Char('Subject', size=1024)
    domain = fields.Char('Domain', size=1024)
    channel = fields.Char('Channel', size=64)
    type = fields.Char('Type', size=256)
    tags = fields.Char('Tags', size=256)
    url = fields.Char('URL', size=512)
    created_at = fields.Datetime('Created at')
    updated_at = fields.Datetime('Updated at')
    assigned_at = fields.Datetime('Assigned at')
    solved_at = fields.Datetime('Solved at')
    gen_timestamp = fields.Integer('Generated Timestamp')
    requester = fields.Char('Requester', size=256)
    from_name = fields.Char('Name', size=256)
    from_email = fields.Char('Email', size=256)
    to_name = fields.Char('Name', size=256)
    to_email = fields.Char('Email', size=256)
    partner_id = fields.Many2one('res.partner', 'Partner')
    tag_ids = fields.Many2many('zendesk.tag', 'rel_zendesk_ticket_tag', 'ticket_id', 'tag_id', 'Tags')
    log_ids = fields.One2many('zendesk.log', 'ticket_id', 'Log')
    state = fields.Selection([('New', 'New'), ('Open', 'Open'), ('Pending', 'Pending'), ('Solved', 'Solved'), ('Closed', 'Closed'), ('Deleted', 'Deleted')], string="state", default='New')


class zendesk_tag(models.Model):
    _name = 'zendesk.tag'
    _log_access = False

    name = fields.Char('Tag', size=64, required=True)


class zendesk_log(models.Model):
    _name = 'zendesk.log'
    _log_access = False
    _order = 'date'

    zendesk_id = fields.Char('Zendesk ID')
    ticket_id = fields.Many2one('zendesk.ticket', 'Ticket')
    date = fields.Datetime('Date')
    public = fields.Boolean('Public')
    from_name = fields.Char('Name', size=256)
    from_email = fields.Char('Email', size=256)
    comment = fields.Text('Comment')
    attachment_ids = fields.One2many('zendesk.log.attachment', 'log_id', 'Attachments')

    @api.model
    def create(self, vals):
        log = super(zendesk_log, self).create(vals)
        if 'from_name' in vals or 'from_email' in vals:
            new_vals = {}
            if vals.get('from_name'):
                new_vals['from_name'] = vals['from_name']
            if vals.get('from_email'):
                new_vals['from_email'] = vals['from_email']
                partners = self.env['res.partner'].search([('email', '=', vals['from_email'])])
                if partners:
                    new_vals['partner_id'] = partners[0]
            log.ticket_id.update(new_vals)

        return log


class zendesk_log_attachment(models.Model):
    _name = 'zendesk.log.attachment'
    _log_access = False

    log_id = fields.Many2one('zendesk.log')
    name = fields.Char('File Name')
    url = fields.Char('URL')
    type = fields.Char('Type', size=64)
    zendesk_id = fields.Char('Zendesk Id')
