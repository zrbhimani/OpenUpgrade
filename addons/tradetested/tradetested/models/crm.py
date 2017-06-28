# -*- coding: utf-8 -*-

from odoo import tools, api, fields, models, _

import logging
_logger = logging.getLogger('Customer Segmentation')


class crm_segmentation(models.Model):
    _name = "crm.segmentation"

    name = fields.Char('Name', size=64, required=True, help='The name of the segmentation.')
    description = fields.Text('Description')
    categ_id = fields.Many2one('res.partner.category', 'Partner Category', required=True, help='The partner category that will be added to partners that match the segmentation criterions after computation.')
    exclusif = fields.Boolean('Exclusive', help='Check if the category is limited to partners that match the segmentation criterions.\nIf checked, remove the category from partners that doesn\'t match segmentation criterions')
    state = fields.Selection([('not running', 'Not Running'), ('running', 'Running')], 'Execution Status', readonly=True, default='not running')
    partner_id = fields.Integer('Max Partner ID processed', default=0)
    segmentation_line = fields.One2many('crm.segmentation.line', 'segmentation_id', 'Criteria', required=True)
    sales_purchase_active = fields.Boolean('Use The Sales Purchase Rules', help='Check if you want to use this tab as part of the segmentation rule. If not checked, the criteria beneath will be ignored')

    @api.model
    def run_segmentation_cron(self):
        segments = self.search([])
        for segment in segments:
            _logger.info('Customer Segmentation Cron Starting')
            segment.run_segmentation()
        return True

    @api.multi
    def run_segmentation(self):
        sobj = self[0]
        mc = self.env['mailchimp.config'].search([('state','=','Connected')])

        _logger.info('Segmentation : %s' % sobj.name)

        where_clause = " WHERE state not in ('cancel','draft') "
        group_clause = "group by partner_id"

        for line in sobj.segmentation_line:
            if line.expr_name == 'total_sale':
                group_clause += " having sum(total_amount) " + line.expr_operator + str(line.expr_value)
                if line.expr_count:
                    group_clause += " AND sum(total_order) >=" + str(line.expr_count)
                if line.expr_repeat:
                    if line.expr_repeat == 'first_time':
                        group_clause += " AND sum(total_order) = 1 "
                    elif line.expr_repeat == 'repeat':
                        group_clause += " AND sum(total_order) > 1 "

                if line.expr_days:
                    where_clause += " AND (current_date - date_order) " + str(line.expr_days_expr) + " " + str(line.expr_days)

            query = '''
                        WITH sales_order_data as
                        (
                            SELECT  DISTINCT ON (partner_id)
                                    id,
                                    partner_id,
                                    date_order,
                                    state,
                                    (SELECT sum(amount_untaxed) from sale_order WHERE partner_id = so.partner_id AND state not in ('draft', 'cancel')) as total_amount,
                                    (SELECT count(*) from sale_order WHERE partner_id = so.partner_id AND state not in ('draft', 'cancel')) as total_order
                            FROM
                                    sale_order so
                            WHERE
                                    state not in ('cancel','draft')
                            ORDER BY
                                    partner_id, date_order DESC, id
                        )
                        SELECT partner_id FROM sales_order_data %s %s''' % (where_clause, group_clause)
            # print query
            self._cr.execute(query)
            partner_ids = [x[0] for x in self._cr.fetchall()]

            _logger.info('Segmentation - Matching Partners %s' % len(partner_ids))

            if not partner_ids:
                # Reset
                if mc:
                    mc.reset_members_interest(crm_seg_id=sobj.id)
                self._cr.execute("DELETE from res_partner_res_partner_category_rel WHERE category_id=%s" % (sobj.categ_id.id))
                return True

            # Remove
            self._cr.execute("""WITH segment_data as(
                            %s
                        )SELECT partner_id from res_partner_res_partner_category_rel rel WHERE category_id=%s and partner_id not in (select partner_id from segment_data)""" %(query, sobj.categ_id.id))

            to_remove = [x[0] for x in self._cr.fetchall()]
            if to_remove:
                self._cr.execute("DELETE from res_partner_res_partner_category_rel WHERE category_id=%s and partner_id in (%s)" %(sobj.categ_id.id, ",".join(map(str, to_remove))))
                if mc:
                    mc.update_members_interest(crm_seg_id=sobj.id, member_ids=to_remove)

            # Add
            self._cr.execute("""WITH segment_data as(%s)
                SELECT partner_id from segment_data WHERE partner_id not in (select partner_id from res_partner_res_partner_category_rel WHERE category_id=%s )
                """ %(query, sobj.categ_id.id))
            to_add = [x[0] for x in self._cr.fetchall()]
            if to_add:
                args_str = ','.join("(%s, %s)" %(sobj.categ_id.id,p) for p in to_add)
                self._cr.execute("INSERT INTO res_partner_res_partner_category_rel (category_id, partner_id) VALUES " + args_str)
                if mc:
                    mc.update_members_interest(crm_seg_id=sobj.id, member_ids=to_add)

        return True

    @api.multi
    def reset_and_export(self):
        mc = self.env['mailchimp.config'].search([('state','=','Connected')])
        if mc:
            mc.reset_members_interest(crm_seg_id=self[0].id)


class crm_segmentation_line(models.Model):
    _name = "crm.segmentation.line"

    name = fields.Char('Rule Name', size=64, required=True)
    segmentation_id = fields.Many2one('crm.segmentation', 'Segmentation')
    expr_operator = fields.Selection([('<', '<'), ('=', '='), ('>', '>')], 'Operator', required=True, default='>')
    expr_value = fields.Float('Value', required=True)
    operator = fields.Selection([('and', 'Mandatory Expression'), ('or', 'Optional Expression')], 'Mandatory / Optional', required=True, default='and')
    expr_name = fields.Selection([('total_sale', 'Total Sale Amount')], 'Control Variable', size=64, required=True, default='total_sale')
    expr_count = fields.Integer('Count of Sales')
    expr_days_expr = fields.Char('Expr')
    expr_days = fields.Integer('Days Since Last Order')
    expr_repeat = fields.Selection([('first_time', 'First Time'), ('repeat', 'Repeat')], 'Frequency')

    def test(self, cr, uid, ids, partner_id):
        expression = {'<': lambda x, y: x < y, '=': lambda x, y: x == y, '>': lambda x, y: x > y}
        ok = False
        lst = self.read(cr, uid, ids)
        for l in lst:
            cr.execute('select * from ir_module_module where name=%s and state=%s', ('account', 'installed'))
            if cr.fetchone():
                if l['expr_name'] == 'total_sale':
                    cr.execute('SELECT SUM(so.amount_untaxed) as amount, count(*) as count FROM sale_order')
                    value = cr.fetchone()[0] or 0.0
                res = expression[l['expr_operator']](value, l['expr_value'])
                if (not res) and (l['operator'] == 'and'):
                    return False
                if res:
                    return True
        return True
