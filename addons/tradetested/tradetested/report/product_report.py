# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo import tools
from openerp import SUPERUSER_ID
from odoo.exceptions import except_orm, ValidationError
import StringIO
import xlsxwriter
import base64, time
import traceback, sys
from datetime import datetime
import math

import logging

_logger = logging.getLogger(__name__)


def median(mylist):
    if not mylist:
        return 0
    sorts = sorted(mylist)
    length = len(sorts)
    if not length % 2:
        return (sorts[length / 2] + sorts[length / 2 - 1]) / 2.0
    return sorts[length / 2]


def update_sql(table, data, identifier):
    sql = list()
    sql.append("UPDATE %s SET " % table)
    sql.append(", ".join("%s = '%s'" % (k, v) if v is not None else "%s = Null" % k for k, v in data.iteritems()))
    sql.append(" WHERE ")
    sql.append(" AND ".join("%s = '%s'" % (k, v) for k, v in identifier.iteritems()))
    sql.append(";")
    return "".join(sql)


class bom_sets_replenishment(models.Model):
    _name = 'bom.sets.replenishment'
    _auto = False

    default_code = fields.Char('SKU')
    name = fields.Char('Name')
    product_id = fields.Many2one('product.product', 'Product')
    qty_available = fields.Float(related='product_id.qty_available', string='Quantity On Hand')
    virtual_available = fields.Float(related='product_id.virtual_available', string='Forecasted Quantity')
    qty_required = fields.Float(compute='_qty_required', string='Required Quantity')
    bom_ids = fields.One2many('mrp.bom', compute='_related_records', string='Bill of Material')
    product_ids = fields.One2many('product.product', compute='_related_records', string='Products')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'bom_sets_replenishment')
        # TODO include bom line in this report
        self._cr.execute("""
            create or replace view bom_sets_replenishment as (
                select
                    distinct p.id as id,
                    t.name,
                    p.default_code as default_code,
                    p.id as product_id
                FROM
                    product_product p,
                    product_template t,
                    mrp_bom b
                WHERE
                    p.id = b.product_id AND
                    p.product_tmpl_id = t.id AND
                    p.active = True
                order by
                    t.name
            )""")

    @api.multi
    def _qty_required(self):
        # context['excl_quote'] = True

        for obj in self:
            qty_req = 0
            bom_headers = self.env['mrp.bom'].search([('bom_line_ids.product_id', '=', obj.product_id.id)])
            for bom in bom_headers:
                set_qty = []
                this_prod_qty = 0
                if len(bom.bom_line_ids) == 1:
                    qty_req = obj.product_id.qty_available
                    continue
                for bom_line in bom.bom_line_ids:
                    if bom_line.product_id.id == obj.product_id.id:
                        this_prod_qty = bom_line.product_qty
                    else:
                        prod_qty = bom_line.product_id.qty_available
                        # other_reserved = bom_pool.search(cr, uid, [('product_id','=',bom_line.product_id.id),('bom_id','!=',False),('id','!=',bom_line.id)])
                        # for res_bom_obj in bom_pool.browse(cr, uid, other_reserved, context=context):
                        #     prod_qty -= (res_bom_obj.bom_id.product_id.qty_available * res_bom_obj.product_qty)

                        possible_qty = bom_line.product_qty and (prod_qty / bom_line.product_qty) or 0.0
                        set_qty.append(possible_qty)
                if set_qty:
                    possible_set = max(set_qty)
                    qty_req += possible_set * this_prod_qty

            qty_req -= obj.product_id.qty_available
            if qty_req < 0:
                qty_req = 0

            obj.qty_required = qty_req

    @api.multi
    def _related_records(self):
        for obj in self:
            boms = self.env['mrp.bom'].search([('product_id', '=', obj.product_id.id)])
            # , ('bom_id', '=', False)

            self.bom_ids = [b.id for b in boms]
            if boms:
                for bom in boms:
                    self.product_ids = [bom.product_id.id] + [bl.product_id.id for bl in bom.bom_line_ids]
            obj.update({
                'bom_ids': self.bom_ids,
                'product_ids': self.product_ids
            })


# Forecast

class product_sales(models.Model):
    _inherit = 'product.sales.data'
    _table = 'product_sales_data'
    _log_access = False
    _order = 'product_id, sequence'

    sequence = fields.Integer('Sequence')

    period = fields.Char('Period', size=128)
    start_date = fields.Date('Start Date')
    end_date = fields.Date('End Date')
    product_id = fields.Many2one('product.product', 'Product')
    categ_id = fields.Many2one('product.category', 'Category')
    product_sales = fields.Integer('Product Sales S(t)')
    unique_sales = fields.Integer('Unique Products Sold U(t)', group_operator="max")
    total_sales = fields.Integer('Total Products Sold C(t)', group_operator="max")
    avg_sales = fields.Float('Av. Per product A(t)', group_operator="max")
    percent_of_avg = fields.Float('% of average V(t)', group_operator="avg")
    avg_for_estimate = fields.Float('Avg for Estimate', group_operator="max")
    forecasted = fields.Boolean('Forecasted')
    incoming_qty = fields.Integer('Incoming Stock')
    remaining_qty = fields.Integer('Remaining Stock')
    purchase_qty = fields.Integer('Purchase Qty')
    error_msg = fields.Char('Err.', size=8)
    method = fields.Char('Method', size=32)



    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False):
        if not orderby:
            orderby = 'start_date desc'
        return super(product_sales, self).read_group(domain, fields, groupby, offset, limit, orderby)


class product_product(models.Model):
    _inherit = 'product.product'

    recent_velocity = fields.Float('Recent Velocity')
    year_velocity = fields.Float('Year Velocity')
    forecast_velocity = fields.Float("Forecast Velocity")
    forecast_model = fields.Char('Model')
    afv_yoy = fields.Float('YOY Growth')
    afv_purchase_qty = fields.Float('Quantity')
    afv_purchase_cost = fields.Float("Cost")
    dist_weeks = fields.Float("Weeks to sell out")
    dist_qty = fields.Float("Distressed Quantity")
    dist_cost = fields.Float("Distressed Cost")
    sales_data = fields.One2many('product.sales.data', 'product_id', 'Sales Data')
    forecast_qty = fields.Float("Forecaset Qty after Lead time")
    gmros = fields.Float('GMROS')
    gmroi = fields.Float('GMROI')
    avg_price = fields.Float('Avg Selling Price')
    avg_stock = fields.Float('Avg Stock')




class report_buying_forecast(models.Model):
    _name = 'report.buying.forecast'
    _auto = False

    default_code = fields.Char('SKU', size=64)
    name = fields.Char('Name', size=256)
    supply_method = fields.Selection([('produce', 'Manufacture'), ('buy', 'Buy')], 'Supply Method', required=True)
    product_id = fields.Many2one('product.product', 'Product')
    company_id = fields.Many2one('res.company', 'Company')
    forecast_qty = fields.Float('Forecast Quantity')
    purchase_qty = fields.Float('Forecast Purchase Quantity')
    moq = fields.Integer('Min. Qty')
    cbm = fields.Float('Product Volume Purchase Quantity')
    cost = fields.Float('Cost')


class leadtime_multiplier(models.TransientModel):
    _name = 'leadtime.multipllier'

    leadtime_multiplier = fields.Float('Lead Time Multiplier')
    leadtime_weeks = fields.Float('Lead Time in Weeks', digits=(20, 1), help="If this value is set, lead time multiplier will be ignored")

    @api.model
    def default_get(self, fields):
        resp = super(leadtime_multiplier, self).default_get(fields)
        lm = self.env["ir.config_parameter"].get_param("leadtime.multiplier")
        resp['leadtime_multiplier'] = lm and float(lm) or lm

        lw = self.env["ir.config_parameter"].get_param("leadtime.weeks")
        resp['leadtime_weeks'] = lw and float(lw) or lw
        return resp

    @api.multi
    def save_leadtime_multiplier(self):
        sobj = self[0]
        self.env["ir.config_parameter"].set_param("leadtime.multiplier", sobj.leadtime_multiplier or 0.000000001)
        self.env["ir.config_parameter"].set_param("leadtime.weeks", sobj.leadtime_weeks or 0.000000001)
        return True


class purchase_calc_export(models.TransientModel):
    _name = 'purchase.calc.export'

    export_xlsx = fields.Binary('Xlsx')
    export_fname = fields.Char('Filename', size=64)

    @api.multi
    def update_and_print_excel(self):
        for prod_id in self._context.get('active_ids'):
            self.env['product.product'].update_sales_data_and_forecast()
        return self.print_excel()

    @api.multi
    def print_excel(self):

        def colnum_string(n):
            div = n
            string = ""
            temp = 0
            while div > 0:
                module = (div - 1) % 26
                string = chr(65 + module) + string
                div = int((div - module) / 26)
            return string

        prod_pool = self.env['product.product']

        buf = StringIO.StringIO()
        workbook = xlsxwriter.Workbook(buf)
        sheet_f = workbook.add_worksheet('Forecast')
        sheet_s = workbook.add_worksheet('Actual Sales')

        sheet_f.hide_gridlines(2)
        sheet_s.hide_gridlines(2)

        bold_left = workbook.add_format({'bold': True, 'align': 'left',})
        bold_center = workbook.add_format({'bold': True, 'align': 'center',})
        bold_right = workbook.add_format({'bold': True, 'align': 'right'})
        bold_right.set_bottom()
        bold_right.set_top()
        merge_format = workbook.add_format({'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        merge_format_right = workbook.add_format({'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter', 'align': 'right',})
        merge_format_bottom = workbook.add_format({'bold': 1, 'bottom': 1, 'align': 'center', 'valign': 'vcenter'})

        bold_left = workbook.add_format({'bold': True, 'align': 'left',})
        bold_center = workbook.add_format({'bold': True, 'align': 'center',})
        bold_right = workbook.add_format({'bold': True, 'align': 'right', 'top': 1, 'bottom': 1})
        bold_90 = workbook.add_format({'bold': True, 'align': 'right', 'bottom': 1, 'rotation': 45, 'num_format': 'DD - MMM'})
        bold_90_right = workbook.add_format({'bold': True, 'align': 'right', 'bottom': 1, 'right': 1, 'rotation': 45, 'num_format': 'DD - MMM'})

        border_right = workbook.add_format({'right': 1})
        border_bottom = workbook.add_format({'bottom': 1})
        border_bottom_right = workbook.add_format({'bottom': 1, 'right': 1})

        merge_format = workbook.add_format({'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        merge_format_wrap = workbook.add_format({'bold': 1, 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': 1})

        bold_rightm = workbook.add_format({'bold': True, 'align': 'right', 'border': 1,})

        cell_borders = workbook.add_format()
        cell_borders.set_bottom()
        cell_borders.set_top()

        cell_borders2 = workbook.add_format()
        cell_borders2.set_right()

        cell_borders4 = workbook.add_format()
        cell_borders4.set_left()

        cell_borders3 = workbook.add_format()
        cell_borders3.set_bottom()
        cell_borders3.set_top()
        cell_borders3.set_right()

        format_currency = workbook.add_format()
        format_currency.set_num_format('"$"#,##0.00;[RED]"$"#,##0.00')

        format_currency_b = workbook.add_format()
        format_currency_b.set_num_format('"$"#,##0;[RED]"$"#,##0')
        format_currency_b.set_bottom()
        format_currency_b.set_top()

        format_currency_br = workbook.add_format()
        format_currency_br.set_num_format('"$"#,##0;[RED]"$"#,##0')
        format_currency_br.set_bottom()
        format_currency_br.set_top()
        format_currency_br.set_right()
        format_currency_br.set_left()

        format_decimal = workbook.add_format()
        format_decimal.set_num_format('_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-')

        format_decimal_b = workbook.add_format()
        format_decimal_b.set_num_format('_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-')
        format_decimal_b.set_bottom()
        format_decimal_b.set_top()

        format_decimal_br = workbook.add_format()
        format_decimal_br.set_num_format('_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-')
        format_decimal_br.set_bottom()
        format_decimal_br.set_top()
        format_decimal_br.set_right()
        format_decimal_br.set_left()

        sheet_f.merge_range('K1:T1', 'Weeks', merge_format)
        sheet_f.write('U1', 'Custom Weeks', merge_format)

        sheet_f.set_row(1, 30)

        sheet_f.set_column('A:A', 15)
        sheet_f.set_column('B:B', 50)
        sheet_f.set_column('C:C', 6.7)
        sheet_f.set_column('D:D', 6.7)
        sheet_f.set_column('E:E', 6.7)
        sheet_f.set_column('F:F', 6.7)
        sheet_f.set_column('G:G', 6.7)
        sheet_f.set_column('H:H', 6.7)
        sheet_f.set_column('I:I', 6.7)
        sheet_f.set_column('J:J', 6.7)

        sheet_f.set_column('K:K', 6.7)
        sheet_f.set_column('L:L', 6.7)
        sheet_f.set_column('M:M', 6.7)
        sheet_f.set_column('N:N', 6.7)
        sheet_f.set_column('O:O', 6.7)
        sheet_f.set_column('P:P', 6.7)
        sheet_f.set_column('Q:Q', 6.7)
        sheet_f.set_column('R:R', 6.7)
        sheet_f.set_column('S:S', 6.7)
        sheet_f.set_column('T:T', 6.7)
        sheet_f.set_column('U:U', 12.23)

        sheet_f.merge_range('A1:A2', 'SKU', merge_format)
        sheet_f.merge_range('B1:B2', 'Product', merge_format)

        sheet_f.merge_range('C1:C2', 'Saleble Qty', merge_format_wrap)
        sheet_f.merge_range('D1:D2', 'Qty on Order', merge_format_wrap)
        sheet_f.merge_range('E1:E2', 'Product Cases 6m', merge_format_wrap)
        sheet_f.merge_range('F1:F2', 'Lead Time (days)', merge_format_wrap)
        sheet_f.merge_range('G1:G2', 'Cubic Volume', merge_format_wrap)
        sheet_f.merge_range('H1:H2', 'Price', merge_format_wrap)
        sheet_f.merge_range('I1:I2', 'Recent Velocity', merge_format_wrap)
        sheet_f.merge_range('J1:J2', 'Recent Velocity Annual', merge_format_wrap)

        sheet_f.write('K2', 12, bold_right)
        sheet_f.write('L2', 16, bold_right)
        sheet_f.write('M2', 20, bold_right)
        sheet_f.write('N2', 24, bold_right)
        sheet_f.write('O2', 28, bold_right)
        sheet_f.write('P2', 32, bold_right)
        sheet_f.write('Q2', 36, bold_right)
        sheet_f.write('R2', 40, bold_right)
        sheet_f.write('S2', 44, bold_right)
        sheet_f.write('T2', 48, bold_right)
        sheet_f.write('U2', 16, bold_rightm)

        # range = "$H$2:$R$" + str(len(context['active_ids'])+2)

        records = str(len(self._context['active_ids']) + 2)

        formula_q = "=CEILING((HLOOKUP(INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1)),$K$2:$T$%s,ROW()-1,FALSE))+(((HLOOKUP((INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1))+4),$K$2:$T$%s,ROW()-1,FALSE))-(HLOOKUP(INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1)),$K$2:$T$%s,ROW()-1,FALSE)))/4)*($U$2-INDEX($K$2:$U$2,0,MATCH($U$2,$K$2:$U$2,1))),1)" % (
            records, records, records)

        supplier_ids = []
        index = 3
        for prod_id in self._context['active_ids']:
            prod = prod_pool.browse(prod_id)
            for seller in prod.seller_ids:
                if seller.name.id not in supplier_ids:
                    supplier_ids.append(seller.name.id)

            index_str = str(index)
            sheet_f.write('A' + str(index), prod.default_code)
            sheet_f.write('B' + str(index), prod.name)
            sheet_f.write('C' + str(index), prod.saleable_qty)
            sheet_f.write('D' + str(index), sum([sd.incoming_qty for sd in prod.sales_data if sd.sequence < 0]))
            sheet_f.write('E' + str(index), sum([c.months_6 for c in prod.case_count_ids]))

            sheet_f.write('F' + str(index), prod.seller_ids and prod.seller_ids[0].delay or 0)
            sheet_f.write('G' + str(index), prod.volume)
            sheet_f.write('H' + str(index), prod.standard_price, format_currency)
            sheet_f.write('I' + str(index), prod.recent_velocity)
            sheet_f.write('J' + str(index), prod.year_velocity, border_right)

            if not prod.sales_data:
                prod_pool.update_sales_data_and_forecast()
                prod.refresh()

            sd = prod.sales_data

            # sheet_f.write('K' + index_str, sd[10].purchase_qty)
            # sheet_f.write('L' + index_str, sd[9].purchase_qty)
            # sheet_f.write('M' + index_str, sd[8].purchase_qty)
            # sheet_f.write('N' + index_str, sd[7].purchase_qty)
            # sheet_f.write('O' + index_str, sd[6].purchase_qty)
            # sheet_f.write('P' + index_str, sd[5].purchase_qty)
            # sheet_f.write('Q' + index_str, sd[4].purchase_qty)
            # sheet_f.write('R' + index_str, sd[3].purchase_qty)
            # sheet_f.write('S' + index_str, sd[2].purchase_qty)
            # sheet_f.write('T' + index_str, sd[1].purchase_qty, cell_borders2)
            #
            # sheet_f.write('U' + index_str, formula_q, cell_borders2)

            index += 1

        vol_range = "$G$3:$G$" + str(index - 1)
        formula_s = "=SUMPRODUCT(%s3:%s" + str(index - 1) + "," + vol_range + ")"

        index_str = str(index)

        sheet_f.merge_range('A' + index_str + ':J' + index_str, 'Total Cubic', merge_format_right)
        for ltr in ['K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
            sheet_f.write(ltr + index_str, formula_s % (ltr, ltr), format_decimal_b)
        sheet_f.write('U' + index_str, formula_s % ('U', 'U'), format_decimal_br)

        # -------
        index += 1

        p_range = "$H$3:$H$" + str(index - 2)
        formula_p = "=SUMPRODUCT(%s3:%s" + str(index - 2) + "," + p_range + ")"

        index_str = str(index)
        sheet_f.merge_range('A' + index_str + ':J' + index_str, 'Total Price', merge_format_right)
        for ltr in ['K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
            sheet_f.write(ltr + index_str, formula_p % (ltr, ltr), format_currency_b)
        sheet_f.write('U' + index_str, formula_p % ('U', 'U'), format_currency_br)

        for supplier in self.env['res.partner'].browse(supplier_ids):
            index += 2
            sheet_f.merge_range('A%s:B%s' % (index, index), 'Supplier', bold_left)
            index += 1
            sheet_f.merge_range('A%s:B%s' % (index, index), supplier.name)

            index += 2
            sheet_f.merge_range('A%s:B%s' % (index, index), 'Ordering Notes', bold_left)
            index += 1
            sheet_f.merge_range('A%s:B%s' % (index, index), supplier.ordering_notes or '')
            index += 1

        # Sheet Actual Sales
        cell_num = workbook.add_format({'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})
        border_right_num = workbook.add_format({'right': 1, 'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})
        border_bottom_num = workbook.add_format({'bottom': 1, 'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})
        border_bottom_num_right = workbook.add_format({'bottom': 1, 'right': 1, 'num_format': '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)'})

        sheet_s.set_row(1, 70)
        sheet_s.set_column('A:A', 15)
        sheet_s.set_column('B:B', 50)

        sheet_s.set_column('C:AB', 3)

        sheet_s.merge_range('A1:A2', 'SKU', merge_format)
        sheet_s.merge_range('B1:B2', 'Product', merge_format)
        sheet_s.merge_range('C1:AB1', '4 Week Period Ending', merge_format_bottom)

        sales_dates = {}
        for prod_id in self._context['active_ids']:
            prod = prod_pool.browse(prod_id)
            for sd in prod.sales_data:
                if sd.sequence > 0:
                    if sd.sequence not in sales_dates:
                        sales_dates[sd.sequence] = sd.end_date
                    elif sd.end_date != sales_dates[sd.sequence]:
                        raise except_orm('Dates are not sync', 'All selected records have different periods/dates, please re-run forecast')

        for x in range(1, 26):
            sheet_s.write(colnum_string(x + 2) + '2', datetime.strptime(sales_dates[x], '%Y-%m-%d'), bold_90)
        sheet_s.write(colnum_string(26 + 2) + '2', datetime.strptime(sales_dates[26], '%Y-%m-%d'), bold_90_right)

        index = 3
        for prod_id in self._context['active_ids']:
            prod = prod_pool.browse(prod_id)
            index_str = str(index)

            if not prod.sales_data:
                prod_pool.update_sales_data_and_forecast()
                prod.refresh()

            sales_data = dict([(sd.sequence, sd.product_sales) for sd in prod.sales_data if sd.sequence > 0])

            if prod_id == self._context['active_ids'][-1]:
                sheet_s.write('A' + index_str, prod.default_code, border_bottom_right)
                sheet_s.write('B' + index_str, prod.name, border_bottom_right)
                for col_ind in range(1, 26):
                    sheet_s.write(colnum_string(col_ind + 2) + index_str, sales_data[col_ind], border_bottom_num)
                sheet_s.write(colnum_string(26 + 2) + index_str, sales_data[26], border_bottom_num_right)
            else:
                sheet_s.write('A' + index_str, prod.default_code, border_right)
                sheet_s.write('B' + index_str, prod.name, border_right)
                for col_ind in range(1, 26):
                    sheet_s.write(colnum_string(col_ind + 2) + index_str, sales_data[col_ind], cell_num)
                sheet_s.write(colnum_string(26 + 2) + index_str, sales_data[26], border_right_num)

            index += 1

        workbook.close()

        sobj = self[0]
        sobj.write({
            'export_xlsx': base64.encodestring(buf.getvalue()),
            'export_fname': 'Purchase_Calc_' + time.strftime('%Y_%m_%d_%H_%M_%S') + '.xlsx'
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.calc.export',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'target': 'new',
            'name': 'Purchase Calc Export'
        }
