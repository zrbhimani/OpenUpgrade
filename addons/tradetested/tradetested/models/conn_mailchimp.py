# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _

import time
import mailchimp3
import json
import requests
import logging
from datetime import datetime
from nameparser.parser import HumanName
from Crypto.Hash import MD5
import tt_fields

_logger = logging.getLogger('Mailchimp')

MC_NAME = 'TradeTested'


class mailchimp_config(models.Model):
    _name = 'mailchimp.config'

    name = fields.Char('Name', size=256, help="Its just for reference, can use any name", default='Mailchimp')
    api_key = fields.Char('API', size=512, states={'Connected': [('readonly', True)]}, required=True)
    list_ids = fields.One2many('mailchimp.list', 'config_id', 'Lists')
    group_ids = fields.One2many('mailchimp.group', 'config_id', 'Segments')
    state = fields.Selection([('New', 'New'), ('Connected', 'Connected'), ('Error', 'Error')], 'Status', default='New')
    exclude_channel_ids = fields.Many2many('sale.channel', 'mailchimp_exclude_channel_rel', 'mailchimp_id', 'channel_id', 'Exclude Sales Channels')

    _mc_lists = {}
    _mc_interests = {}
    _categ_map = {}

    @api.multi
    def reset_to_draft(self):
        return self.write({'state': 'New'})

    @api.multi
    def test_connection(self):
        sobj = self[0]
        mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)
        try:
            mc.list.all()
            self.write({'state': 'Connected'})
        except Exception, e:
            _logger.error(e)
            self.write({'state': 'Error'})

    @api.multi
    def import_lists(self):
        sobj = self[0]
        mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)
        lists = mc.list.all()
        for list in lists['lists']:
            vals = {'name': list['name'], 'mailchimp_id': list['id'], 'config_id': sobj.id}
            list_exist = self.env['mailchimp.list'].search([('mailchimp_id', '=', vals['mailchimp_id'])])
            if not list_exist:
                self.env['mailchimp.list'].create(vals)

    @api.multi
    def import_groups(self):
        sobj = self[0]
        mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)
        for lst in sobj.list_ids:
            category_data = mc.category.all(lst.mailchimp_id)
            for category in category_data['categories']:
                interest_data = mc.interest.all(lst.mailchimp_id, category['id'])
                for interest in interest_data['interests']:
                    vals = {
                        'name': interest['name'],
                        'config_id': sobj.id,
                        'list_id': lst.id,
                        'mailchimp_id': interest['id'],
                        'category_id': interest['category_id'],
                        'category_name': category['title'],
                    }
                    grp_exist = self.env['mailchimp.group'].search([('category_id', '=', vals['category_id']), ('mailchimp_id', '=', vals['mailchimp_id']), ('list_id', '=', vals['list_id'])])
                    if not grp_exist:
                        self.env['mailchimp.group'].create(vals)
                    else:
                        grp_exist.write(vals)
        return True

    @api.multi
    def import_members(self):
        sobj = self[0]
        mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)

        for lst in sobj.list_ids:
            if not lst.company_id:
                continue

            start = 0
            limit = 2000
            total = 1
            while start < total:
                data = mc.member.all(lst.mailchimp_id, offset=start, count=limit, fields='total_items,members.email_address,members.id,members.unique_email_id,members.status', since_last_changed='2016-04-05 00:00:00')
                total = data['total_items']
                if total == 0:
                    break;

                start += limit

                self._cr.execute("SELECT leid from mailchimp_member WHERE leid in (%s)" % ",".join(map(lambda x: "'" + x + "'", [x['id'] for x in data['members']])))
                exist_ids = [rec[0].replace('-', '') for rec in self._cr.fetchall()]

                self._cr.execute("SELECT email, id from res_partner WHERE email in (%s)" % ",".join(map(lambda x: "'" + x + "'", [x['email_address'].replace("'", "''") for x in data['members']])))
                partner_dict = dict([(rec[0], rec[1]) for rec in self._cr.fetchall()])

                for member in data['members']:
                    if member['id'] not in exist_ids:
                        if member['email_address'] not in partner_dict:
                            continue
                        member_vals = {'partner_id': partner_dict[member['email_address']], 'list_id': lst.id, 'leid': member['id'], 'euid': member['unique_email_id'], 'status': member['status']}
                        self.env['mailchimp.member'].create(member_vals)
                self._cr.commit()

    @api.multi
    def _prepare_data(self, action, company_id, email, name=False, categ_ids=False):
        if not self._mc_lists or not self._mc_interests or not self._categ_map:

            sobj = self[0]

            for lst in sobj.list_ids:
                if lst.company_id:
                    self._mc_lists[lst.company_id.id] = lst

                if lst.company_id.id not in self._mc_interests:
                    self._mc_interests[lst.company_id.id] = {}

                for grp in sobj.group_ids:
                    if grp.list_id.id == lst.id:
                        self._mc_interests[lst.company_id.id][grp.mailchimp_id] = False

            cat_pool = self.env['res.partner.category']
            self._categ_map = dict([cat.id, cat] for cat in cat_pool.search([]))

        if company_id not in self._mc_lists:
            return False

        if not email:
            return False

        email_hash = MD5.new(email.lower()).hexdigest()
        res = {
            'action': action,
            'entity_params': ['lists', self._mc_lists[company_id].mailchimp_id, 'members', email_hash],
            'data': {"email_address": email}
        }

        if action == 'create_or_update':
            res['data']["status_if_new"] = "subscribed"

        if name:
            hn = HumanName(name)
            res['data']['merge_fields'] = {"FNAME": hn.first, "LNAME": hn.last}

        if categ_ids != False:
            res['data']['interests'] = self._mc_interests[company_id].copy()

            for categ_id in categ_ids:
                for group in self._categ_map[categ_id].group_ids:
                    if group.list_id.id == self._mc_lists[company_id].id:
                        res['data']['interests'][group.mailchimp_id] = True
        return res

    @api.multi
    def export_all(self):
        _logger.info('Mailchimp Cron Start')

        ids = self.search([('state', '=', 'Connected')])
        if not ids:
            return False
        assert len(ids) == 1
        sobj = self[0]

        channel_filter = ''
        if sobj.exclude_channel_ids:
            channel_filter = ' AND so.channel not in (%s)' % ','.join(["'" + c.code + "'" for c in sobj.exclude_channel_ids])

        self._cr.execute("""SELECT
                                    distinct ON (so.partner_id)
                                    p.name as partner_name,
                                    p.email as email,
                                    so.company_id as company_id,
                                    so.id as sale_id,
                                    array(select category_id from res_partner_res_partner_category_rel cat WHERE cat.partner_id = p.id) as categ_ids
                                FROM
                                    sale_order so, res_partner p
                                WHERE
                                    so.partner_id=p.id AND
                                    so.state not in ('draft','cancel') AND
                                    p.email is not null AND
                                    mailchimp_export_date is not null %s """ % channel_filter)
        so_data = self._cr.dictfetchall()

        batch_operations = []
        for rec in so_data:

            member_data = sobj._prepare_data('create_or_update', rec['company_id'], rec['email'], rec['partner_name'], rec['categ_ids'])
            if member_data:
                batch_operations.append(member_data)

        if batch_operations:
            mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)
            resp = mc.batches.execute(batch_operations)
            self.env['mailchimp.batch'].create({'batch_id': resp['id']})

        return True

    @api.model
    def export_member(self, partner_id=False, company_id=False):

        sobj = self[0]
        mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)

        partner = self.env['res.partner'].browse(partner_id)

        partner_channel = [o.channel for o in partner.sale_order_ids if o.state != 'cancel']
        if partner_channel:
            if not list(set(partner_channel) - set([c.code for c in sobj.exclude_channel_ids])):
                return  # Excluded from Export

        member_data = sobj._prepare_data('create_or_update', company_id, partner.email, partner.name, [c.id for c in partner.category_id])
        if member_data:
            resp = mc.member.create_or_update(member_data['entity_params'][1], member_id=member_data['entity_params'][3], data=member_data['data'])
            member_ids = self.env['mailchimp.member'].search([('list_id', '=', self._mc_lists[company_id].id), ('partner_id', '=', partner.id)])
            if not member_ids:
                self.env['mailchimp.member'].create({'status': resp['status'], 'leid': resp['id'], 'euid': resp['unique_email_id'], 'list_id': self._mc_lists[company_id].id, 'partner_id': partner.id})
            else:
                member = self.env['mailchimp.member'].browse(member_ids[0])
                if member.status != resp['status']:
                    self.env['mailchimp.member'].write({'status': resp['status']})

    @api.multi
    def reset_members_interest(self, crm_seg_id=False):
        crm_seg = self.env['crm.segmentation'].browse(crm_seg_id)
        self._cr.execute("select partner_id from res_partner_res_partner_category_rel WHERE category_id=%s" % crm_seg.categ_id.id)
        partner_ids = [x[0] for x in self._cr.fetchall()]
        if not partner_ids:
            return

        self[0].update_members_interest(crm_seg_id=crm_seg_id, member_ids=partner_ids)

    @api.multi
    def update_members_interest(self, crm_seg_id=False, member_ids=[]):
        sobj = self[0]
        self._cr.execute("""
                    SELECT
                        DISTINCT ON (p.id)
                        p.id as partner_id,
                        p.email as email,
                        so.id as order_id,
                        so.company_id as company_id,
                        array(select category_id from res_partner_res_partner_category_rel cat WHERE cat.partner_id = p.id) as categ_ids
                    FROM
                        res_partner p
                    JOIN
                        sale_order so on (so.partner_id = p.id)
                    WHERE
                        p.id in (%s) AND p.email is not null
                    ORDER BY
                    p.id, so.date_order DESC;""" % (",".join(map(str, member_ids))))

        operation_batch = []
        for rec in self._cr.dictfetchall():

            member_data = sobj._prepare_data('update', rec['company_id'], rec['email'], name=False, categ_ids=rec['categ_ids'])
            if member_data:
                operation_batch.append(member_data)

        if operation_batch:
            mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)
            resp = mc.batches.execute(operation_batch)
            self.env['mailchimp.batch'].create({'batch_id': resp['id']})

    @api.model
    def mailchimp_export_cron(self, ids=None):
        self._mc_lists = {}
        self._mc_interests = {}
        self._categ_map = {}

        _logger.info('Mailchimp Cron Start')

        sobjs = self.search([('state', '=', 'Connected')])
        if not sobjs:
            return False
        assert len(sobjs) == 1
        sobj = sobjs

        try:
            batch = self.env['mailchimp.batch'].search([('proc_date', '=', False)])
            batch.get_batch_status()
        except Exception, e:
            _logger.error(e)

        mc = mailchimp3.MailChimp(MC_NAME, sobj.api_key)

        channel_filter = ''
        if sobj.exclude_channel_ids:
            channel_filter = ' AND so.channel not in (%s)' % ','.join(["'" + c.code + "'" for c in sobj.exclude_channel_ids])

        batch_operations = []

        # Find Sale Orders
        self._cr.execute("""SELECT
                                    distinct ON (so.partner_id)
                                    p.name as partner_name,
                                    p.email as email,
                                    so.company_id as company_id,
                                    so.id as sale_id,
                                    array(select category_id from res_partner_res_partner_category_rel cat WHERE cat.partner_id = p.id) as categ_ids
                                FROM
                                    sale_order so, res_partner p
                                WHERE
                                    so.partner_id=p.id AND
                                    so.state not in ('draft','cancel') AND
                                    p.email is not null AND
                                    mailchimp_export_date is null %s """ % channel_filter)
        so_data = self._cr.dictfetchall()

        # Find Partners
        self._cr.execute("""SELECT
                                    DISTINCT ON (p.id)
                                    p.name as partner_name,
                                    p.email as email,
                                    so.company_id as company_id,
                                    p.id as partner_id,
                                    array(select category_id from res_partner_res_partner_category_rel cat WHERE cat.partner_id = p.id) as categ_ids
                                FROM
                                    res_partner p
                                JOIN
                                    sale_order so on (so.partner_id = p.id and so.state not in ('draft','cancel') %s)
                                WHERE
                                    p.mc_export=True and p.email is not null
                                ORDER BY
                                    p.id, so.date_order DESC; """ % channel_filter)
        partner_data = self._cr.dictfetchall()

        order_exported = []
        partner_exported = []

        for rec in so_data + partner_data:

            member_data = sobj._prepare_data('create_or_update', rec['company_id'], rec['email'], rec['partner_name'], rec['categ_ids'])

            if member_data:
                batch_operations.append(member_data)

            if rec.get('sale_id'):
                order_exported.append(rec['sale_id'])

            if rec.get('partner_id'):
                partner_exported.append(rec['partner_id'])

        if batch_operations:
            resp = mc.batches.execute(batch_operations)
            self.env['mailchimp.batch'].create({'batch_id': resp['id']})

            if order_exported:
                self._cr.execute("UPDATE sale_order set mailchimp_export_date='%s' WHERE id in (%s)" % (time.strftime('%Y-%m-%d %H:%M:%S'), ','.join(map(str, order_exported))))

            if partner_exported:
                self._cr.execute("UPDATE res_partner set mc_export=False WHERE id in (%s)" % (','.join(map(str, partner_exported))))


class mailchimp_list(models.Model):
    _name = 'mailchimp.list'

    config_id = fields.Many2one('mailchimp.config', 'Configuration', required=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', 'Company')
    mailchimp_id = fields.Char('ID', size=16)
    name = fields.Char('Name', size=64)


class mailchimp_member(models.Model):
    _name = 'mailchimp.member'
    _log_access = False

    partner_id = fields.Many2one('res.partner', 'Customer', ondelete='cascade')
    company_id = fields.Many2one('res.company', related='list_id.company_id', string='Company')
    list_id = fields.Many2one('mailchimp.list', 'List')
    leid = tt_fields.Uuid('ID/Email Hash')
    euid = fields.Char('Unique Email ID', size=16)
    status = fields.Char('Status')

    _sql_constraints = [
        ('record_uniq', 'unique (partner_id, list_id, euid)', 'Duplicates are not allowed')
    ]


class mailchimp_group(models.Model):
    _name = 'mailchimp.group'

    name = fields.Char('Name')
    config_id = fields.Many2one('mailchimp.config', 'Config')
    list_id = fields.Many2one('mailchimp.list', 'List')
    mailchimp_id = fields.Char('Mailchimp ID')
    category_id = fields.Char('Category ID')
    category_name = fields.Char('Category')
    subscribers = fields.Integer('Subscribers')

    @api.multi
    def name_get(self):
        result = []
        for grp in self:
            if grp.list_id:
                result.append((grp.id, grp.list_id.name + ' - ' + grp.name))
            else:
                result.append((grp.id, grp.name))
        return result


class mailchimp_batch(models.Model):
    _name = 'mailchimp.batch'
    _log_access = False
    _order = 'post_date desc'

    batch_id = fields.Char('Batch ID')
    post_date = fields.Datetime('POST Date', default=lambda self: time.strftime('%Y-%m-%d %H:%M:%S'))
    proc_date = fields.Datetime('Batch Completed at')
    resp = fields.Serialized('Response')

    @api.multi
    def get_batch_status(self):

        mcs = self.env['mailchimp.config'].search([('state', '=', 'Connected')])
        if not mcs:
            return False
        assert len(mcs) == 1

        mcobj = mcs[0]
        mc = mailchimp3.MailChimp(MC_NAME, mcobj.api_key)

        for batch in self:
            resp = mc.batches.get(batch.batch_id)
            if resp['status'] == 'finished' and resp['response_body_url']:
                # Don't Download response : too big files to process
                # resp_gzip = requests.get(resp['response_body_url'])
                # result = gzip.GzipFile(fileobj=StringIO(resp_gzip.content)).read()
                # result = result.strip(' \t\r\n\0')
                # resp_dict = json.loads(result[result.find('['):])
                batch.write({'resp': json.dumps(resp), 'proc_date': datetime.strptime(resp['completed_at'][:-6], '%Y-%m-%dT%H:%M:%S')})
            else:
                batch.write({'resp': json.dumps(resp)})


class res_partner_category(models.Model):
    _inherit = 'res.partner.category'
    group_ids = fields.Many2many('mailchimp.group', 'mailchimp_groups_rel', 'crm_seg_id', 'mc_grp_id', 'Mailchimp Groups')
