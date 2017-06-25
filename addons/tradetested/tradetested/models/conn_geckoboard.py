# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError
from datetime import datetime
from dateutil.relativedelta import relativedelta
import json, requests
from common import convert_tz
import time
import simplejson as json
import logging

_logger = logging.getLogger('Geckoboard')

gecko_url = 'https://push.geckoboard.com/v1/send/'


class geckoboard_widget(models.Model):
    _name = 'geckoboard.widget'

    geckoboard_id = fields.Many2one('geckoboard.config', 'Geckoboard')
    name = fields.Char('Name', size=256)
    widget_api = fields.Char('Widget Api Key', size=256)
    sync = fields.Boolean('Sync', default=True)
    function = fields.Char('Function', size=256)
    context = fields.Char('Context', default='{}')
    result = fields.Serialized(compute='_result', string="Result")

    @api.multi
    def _result(self):
        for rec in self:
            data_dict = eval('rec.geckoboard_id.' + rec.function + '()')
            rec.result = json.dumps(data_dict)

    @api.multi
    def export_widget(self):
        sobj = self[0]
        data_dict = eval('sobj.geckoboard_id.' + sobj.function + '()')
        data_dict['api_key'] = sobj.geckoboard_id.api_key
        resp = requests.post(gecko_url + sobj.widget_api, data=json.dumps(data_dict), headers={'Content-type': 'application/json', 'Accept': 'text/plain'})
        if resp.status_code != 200:
            _logger.error('Widget %s, Response: %s, %s' % (sobj.name, resp.reason, resp.text))


class geckoboard_config(models.Model):
    _name = 'geckoboard.config'
    _description = "Geckboard"

    api_key = fields.Char('API Key', size=512, required=True)
    widget_ids = fields.One2many('geckoboard.widget', 'geckoboard_id', 'Widgets')

    @api.multi
    def synchronize_all(self):
        ids = self.search([])

        assert len(ids) == 1, 'Should have only one Geckoboard configuration'

        headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
        sobj = self[0]

        for widget in sobj.widget_ids:
            if not widget.sync:
                continue
            ctx = self._context.copy()
            ctx.update(eval(widget.context))
            data_dict = eval('widget.' + widget.function + '(ctx)')
            data_dict['api_key'] = sobj.api_key
            resp = requests.post(gecko_url + widget.widget_api, data=json.dumps(data_dict), headers=headers)
            if resp.status_code != 200:
                _logger.error('Widget %s, Response: %s, %s' % (widget.name, resp.reason, resp.text))
        return True

    @api.model
    def daily_total_sale(self):
        today_local = convert_tz(datetime.today(), 'Pacific/Auckland')
        self._cr.execute("select sum(amount_total) as amount_total from sale_order WHERE state not in ('cancel','quote') AND date_order = '%s' group by date_order " % (today_local.strftime('%Y-%m-%d')))
        resp = self._cr.dictfetchall()

        amount_total = 0
        if resp and resp[0].get('amount_total'):
            amount_total = round(resp[0]['amount_total'])

        return {"data": {"item": [{'text': "NZD", 'prefix': '$', 'value': amount_total}]}}

    @api.model
    def total_cases(self):
        cs_team_ids = self.env['res.users'].search([('team_id.name', 'in', ['Customer Service', 'Showroom'])])
        self._cr.execute('''
            SELECT
                COUNT(*) as total
            FROM
                crm_helpdesk ch LEFT JOIN crm_lead_tag clt on ch.categ_id  = clt.id
            WHERE
                state in ('draft','open') AND ch.user_id in (%s) AND
                ( clt.export_to_geckoboard = True OR ch.categ_id is null )''' % (",".join(map(str, [c.id for c in cs_team_ids]))))
        resp = self._cr.dictfetchall()

        total = 0
        if resp and resp[0].get('total'):
            total = resp[0]['total']

        return {"data": {"item": [{'text': "Total Cases", 'value': total}]}}

    @api.model
    def daily_total_sale_by_users(self):
        items = []
        user_pool = self.env['res.users']
        # Total Sales
        today_local = convert_tz(datetime.today(), 'Pacific/Auckland')

        last_seven_days = []
        day = today_local
        for i in range(7):
            day = day - relativedelta(days=1)
            last_seven_days.append(day.strftime('%Y-%m-%d'))

        showroom_users = self.env['res.users'].search([('team_id.name', '=', 'Showroom')])
        for user_name, user_ids in [('Roydon', [15]), ('Wayne', [16]), ('Showroom', [s.id for s in showroom_users])]:
            self._cr.execute(
                '''select sum(amount_total_nzd) as amount_total from sale_order WHERE state not in ('cancel','quote') AND user_id in (%s) AND date_order = '%s' group by date_order''' % (','.join(map(str, user_ids)), today_local))
            resp = self._cr.dictfetchall()
            if resp and resp[0].get('amount_total'):
                resp = resp[0]
            else:
                resp = {'amount_total': 0}

            # PROJECTED
            self._cr.execute(
                '''select sum(amount_total_nzd) as amount_total from sale_order WHERE state not in ('cancel') AND user_id in (%s) AND date_order in ('%s') group by date_order''' % (','.join(map(str, user_ids)), "','".join(last_seven_days)))
            resp_proj = [x[0] for x in self._cr.fetchall()]
            resp_proj = sorted(resp_proj)
            if resp_proj:
                projected = resp_proj[-1]
            else:
                projected = 0

            total_bullet = {
                "label": user_name,
                "sublabel": "$ " + str(int(resp['amount_total'])) + " NZD",
                "axis": {"point": ["0", "4000", "8000", "12000", "16000", "20000"]},
                "range": [{"color": "red", "start": 0, "end": 4000}, {"color": "amber", "start": 4001, "end": 8000}, {"color": "green", "start": 80001, "end": 20000}],
                "measure": {"current": {"start": "0", "end": resp['amount_total']},
                            "projected": {"start": "0", "end": projected}},
            }
            items.append(total_bullet)

        return {"data": {"orientation": "vertical", "item": items}}

    @api.model
    def daily_total_sale_bullet(self):
        today_local = convert_tz(datetime.today(), 'Pacific/Auckland')

        last_seven_days = []
        day = today_local
        for i in range(7):
            day = day - relativedelta(days=1)
            last_seven_days.append(day.strftime('%Y-%m-%d'))

        self._cr.execute('''select sum(amount_total_nzd) as amount_total from sale_order WHERE state not in ('cancel','quote') and date_order = '%s' group by date_order''' % (today_local))
        resp = self._cr.dictfetchall()
        if resp and resp[0].get('amount_total'):
            resp = resp[0]
        else:
            resp = {'amount_total': 0}

        self._cr.execute('''select sum(amount_total_nzd) as amount_total from sale_order WHERE state not in ('cancel','quote') AND date_order in ('%s') group by date_order''' % ("','".join(last_seven_days)))
        resp_proj = [x[0] for x in self._cr.fetchall()]
        resp_proj = sorted(resp_proj)
        if resp_proj:
            projected = resp_proj[-1]
        else:
            projected = 0

        total_bullet = {
            "label": "Total",
            "sublabel": "$ " + str(int(resp['amount_total'])) + " NZD",
            "axis": {"point": ["0", "10000", "20000", "30000", "40000", "50000", "60000", ]},
            "range": [{"color": "red", "start": 0, "end": 20000}, {"color": "amber", "start": 20001, "end": 40000}, {"color": "green", "start": 40001, "end": 60000}],
            "measure": {"current": {"start": "0", "end": resp['amount_total']},
                        "projected": {"start": "0", "end": projected or 0}},
        }

        return {"data": {"orientation": "vertical", "item": total_bullet}}

    @api.model
    def this_week_sale(self):
        today_local = convert_tz(datetime.today(), 'Pacific/Auckland')

        week_days = []
        day = today_local
        week_days.append(day.strftime('%Y-%m-%d'))
        while (day.weekday() != 0):
            day = day - relativedelta(days=1)
            week_days.append(day.strftime('%Y-%m-%d'))

        self._cr.execute('''select sum(amount_total_nzd) as amount_total from sale_order WHERE state not in ('cancel','quote') AND date_order in ('%s')''' % ("','".join(week_days)))
        resp = self._cr.dictfetchall()

        amount_total = 0
        if resp and resp[0]['amount_total']:
            amount_total = round(resp[0]['amount_total'])

        return {"data": {"item": [{'text': "NZD", 'prefix': '$', 'value': amount_total}]}}

    @api.model
    def cases_requiring_action(self):
        cs_team_ids = self.env['res.users'].search([('team_id.name', 'in', ['Customer Service', 'Showroom'])])
        today_local = convert_tz(datetime.today(), 'Pacific/Auckland')
        today_local_str = today_local.strftime('%Y-%m-%d')
        self._cr.execute('''SELECT
                                    count(*) as total
                                FROM
                                    crm_helpdesk tkt LEFT JOIN crm_lead_tag cat ON tkt.categ_id = cat.id
                                WHERE
                                    state in ('draft','open') AND
                                    tkt.user_id in (%s) AND
                                    (
                                        date_deadline <= ('%s') OR
                                        (
                                            date_deadline is null AND
                                            (SELECT count(*) from res_activity_log WHERE res_model='crm.helpdesk' and res_id=tkt.id and date::date='%s' ) = 0
                                        )
                                    ) AND
                                    (cat.export_to_geckoboard = True OR tkt.categ_id is null)
                            ''' % (",".join(map(str, [c.id for c in cs_team_ids])), today_local_str, today_local_str))

        resp = self._cr.dictfetchall()
        return {"data": {"item": [{'value': resp[0]['total'], "label": "Overdue Cases"}]}}

    @api.model
    def payments_to_review(self):
        self._cr.execute('''select
                                count(*) as payments_to_review
                            from
                                sale_order_payment
                            where
                                reviewed = False or reviewed is null
                            ''')
        resp = self._cr.dictfetchall()
        return {"data": {"item": [{'value': resp[0]['payments_to_review'], "label": "Payments to Review"}]}}

    @api.model
    def count_scs_soh_discrepancies(self):
        sohs = self.env['scs.soh'].search([], limit=1)
        soh_comp_count = 0
        if sohs:
            soh_comp_count = self.env['scs.soh.comparison'].search_count([('soh_id', '=', sohs[0].id), '|', ('variance', '>', 1), ('variance', '<', -1)])

        items = [{'value': soh_comp_count, "label": "Stock Discrepancies"}]
        return {"data": {"item": items}}

    @api.model
    def case_close_rate(self):

        self._cr.execute("select count(*) from crm_helpdesk WHERE section_id=2 AND state='done' AND create_date >now() - interval '7 days'")
        close_count = self._cr.fetchone()[0]

        self._cr.execute("select count(*) from crm_helpdesk WHERE section_id=2 AND state!='cancel' AND create_date >now() - interval '7 days'")
        create_count = self._cr.fetchone()[0]

        rate = 0
        if create_count > 0:
            rate = (float(close_count) / float(create_count)) * 100.0

        return {"data": {"item": [{'value': round(rate), 'prefix': '%'}]}}

    @api.model
    def active_products(self):
        self._cr.execute("select count(*) as active_products from product_product p, product_template t WHERE p.product_tmpl_id = t.id AND p.active=True AND t.sale_ok=True and t.state in ('sellable','end')");
        resp = self._cr.dictfetchall()

        return {"data": {"item": [{'value': resp[0]['active_products'] or 0, "label": "Active Products"}]}}

    @api.model
    def out_of_stock_products(self):
        self._cr.execute("""SELECT
                                (
                                    SELECT count(*) from product_product p, product_template t
                                    WHERE p.product_tmpl_id = t.id AND
                                            p.active=True AND
                                            t.sale_ok=True AND
                                            t.state in ('sellable','end') /* AND 
                                            p.virtual_available_db<=0*/
                                )/(
                                    SELECT count(*) from product_product p, product_template t
                                    WHERE p.product_tmpl_id = t.id AND
                                            p.active=True AND
                                            t.sale_ok=True AND
                                            t.state in ('sellable','end')
                                )::float * 100 as out_of_stock_products""")
        resp = self._cr.dictfetchall()
        return {"data": {"item": [{'value': resp[0]['out_of_stock_products'] or 0, 'prefix': '%', "label": "Out of Stock Products"}]}}

    @api.multi
    def cases_count(self):
        if not self._context.get('team'):
            raise UserError(_('Error!'), ("Please specify team in context."))

        team = self._context.get('team')
        team_case = self.env['crm.helpdesk'].search(['|', ('user_id.default_section_id.name', '=', team), ('section_id.name', '=', team)])
        total = len(team_case)

        items = [{'value': total, "label": team + ' Cases'}]
        return {"data": {"item": items}}
