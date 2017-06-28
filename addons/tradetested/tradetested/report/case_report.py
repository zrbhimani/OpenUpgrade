# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo import tools

AVAILABLE_STATES = [
    ('draft', 'Draft'),
    ('open', 'Open'),
    ('cancel', 'Cancelled'),
    ('done', 'Closed'),
    ('pending', 'Pending')
]


class crm_helpdesk_report(models.Model):
    _name = 'crm.helpdesk.report'
    _auto = False

    name = fields.Char('Year', size=64, required=False, readonly=True)
    user_id = fields.Many2one('res.users', 'User', readonly=True)
    section_id = fields.Many2one('crm.team', 'Section', readonly=True)
    month = fields.Selection([('01', 'January'), ('02', 'February'), \
                              ('03', 'March'), ('04', 'April'), \
                              ('05', 'May'), ('06', 'June'), \
                              ('07', 'July'), ('08', 'August'), \
                              ('09', 'September'), ('10', 'October'), \
                              ('11', 'November'), ('12', 'December')], 'Month', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Partner', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    date_deadline = fields.Date('Deadline', index=True)
    priority = fields.Selection([('5', 'Lowest'), ('4', 'Low'), \
                                 ('3', 'Normal'), ('2', 'High'), ('1', 'Highest')], 'Priority')
    delay_close = fields.Float('Delay to Close', digits=(16, 2), readonly=True, group_operator="avg")
    nbr = fields.Integer('# of Cases', readonly=True)
    email = fields.Integer('# Emails', size=128, readonly=True)
    delay_expected = fields.Float('Overpassed Deadline', digits=(16, 2), readonly=True, group_operator="avg")
    planned_cost = fields.Float('Planned Costs')
    day = fields.Char('Day', size=128, readonly=True)
    state = fields.Selection(AVAILABLE_STATES, 'Status', size=16, readonly=True)
    categ_id = fields.Many2one('crm.lead.tag', 'Category')
    resolution_ids = fields.Many2many('crm.helpdesk.resolution', 'rel_crm_helpdesk_resolution', 'case_id', 'resolution_id', 'Resolution')
    resolution_id = fields.Many2one('crm.helpdesk.resolution', 'Resolution')
    product_id = fields.Many2one('product.product', 'Product')
    product_categ_id = fields.Many2one('product.category', 'Product Category')
    year_open = fields.Char('Year Open', size=16)
    month_open = fields.Char('Month Open', size=16)
    week_open = fields.Char('Year Open', size=16)
    week_closed = fields.Char('Week Closed', size=16)

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'crm_helpdesk_report')
        self._cr.execute("""
            create or replace view crm_helpdesk_report as (
                select
                    c.id as id,
                    to_char(c.date_closed, 'YYYY') as name,
                    to_char(c.date_closed, 'MM') as month,
                    to_char(c.date_closed, 'YYYY-MM-DD') as day,
                    to_char(c.create_date, 'YYYY-MM-DD') as create_date,
                    to_char(c.date_closed, 'YYYY-mm-dd') as date_closed,
                    to_char(c.create_date, 'YYYY') as year_open,
                    to_char(c.create_date, 'MM') as month_open,
                    to_char(c.create_date, 'IW') as week_open,
                    to_char(c.date_closed, 'IW') as week_closed,
                    c.state,
                    c.user_id,
                    c.section_id,
                    c.partner_id,
                    c.company_id,
                    c.priority,
                    c.date_deadline,
                    c.categ_id,

                    c.planned_cost,
                    c.resolution_id,
                    c.product_id,
                    c.product_categ_id,
                    count(*) as nbr,
                    extract('epoch' from (c.date_closed-c.create_date))/(3600*24) as  delay_close,
                    (SELECT count(id) FROM mail_message WHERE model='crm.helpdesk' AND res_id=c.id AND type = 'email') AS email,
                    abs(avg(extract('epoch' from (c.date_deadline - c.date_closed)))/(3600*24)) as delay_expected
                from
                    crm_helpdesk c
                where c.active = 'true'
                group by to_char(c.date, 'YYYY'), to_char(c.date, 'MM'),to_char(c.date, 'YYYY-MM-DD'),\
                     c.state, c.user_id, c.section_id, c.priority,\
                     c.partner_id,c.company_id,c.date_deadline,c.create_date,c.date,c.date_closed,\
                     c.categ_id,c.planned_cost,c.id, c.product_categ_id, c.product_id
            )""")

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        resp = super(crm_helpdesk_report, self).read_group(domain, fields, groupby, offset, limit, orderby)
        if 'categ_id' in groupby and 'year_open' not in groupby and 'month_open' not in groupby:
            resp = sorted(resp, key=lambda x: x['nbr'], reverse=True)
        return resp

class crm_helpdesk_count_report(models.Model):
    _name = 'crm.helpdesk.count.report'
    _auto = False
    _table = 'crm_helpdesk_count_report'
    _order = 'months_3 desc, months_6 desc, months_12 desc, total desc'

    product_id = fields.Many2one('product.product', 'Product')
    categ_id = fields.Many2one('crm.lead.tag', 'Issue')
    months_3 = fields.Integer('3 Months')
    months_6 = fields.Integer('6 Months')
    months_12 = fields.Integer('12 Months')
    total = fields.Integer('All Time')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'crm_helpdesk_count_report')
        self._cr.execute("""
            create or replace view crm_helpdesk_count_report as (
                select
                    min(ch.id) as id,
                    ch.product_id,
                    ch.categ_id,
                    count(CASE WHEN ch.create_date between date_trunc('month', now()) - interval '3 month' and now() THEN ch.id END ) as months_3,
                    count(CASE WHEN ch.create_date between date_trunc('month', now()) - interval '6 month' and now() THEN ch.id END ) as months_6,
                    count(CASE WHEN ch.create_date between date_trunc('month', now()) - interval '12 month' and now() THEN ch.id END ) as months_12,
                    count(*) as total
                from
                    crm_helpdesk ch
                WHERE
                    state != 'cancel'
                group by
                    product_id,
                    categ_id)""")
