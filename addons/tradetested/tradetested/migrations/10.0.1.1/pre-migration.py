# -*- coding: utf-8 -*-
# © 2017 Therp BV <http://therp.nl>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from openupgradelib import openupgrade



def delete_old_module_views(cr, module):
    cr.execute("""
        DELETE from ir_ui_view where id in (select res_id from ir_model_data where module='%s' and model = 'ir.ui.view');
    """ %(module))



_xmlid_renames = [
    ('crm_helpdesk.view_crm_case_helpdesk_filter', 'tradetested.view_crm_case_helpdesk_filter'),
    ('crm_helpdesk.crm_case_helpdesk_calendar_view', 'tradetested.crm_case_helpdesk_calendar_view'),
    ('crm_helpdesk.crm_case_form_view_helpdesk', 'tradetested.crm_case_form_view_helpdesk'),
    ('crm_helpdesk.crm_case_tree_view_helpdesk', 'tradetested.crm_case_tree_view_helpdesk'),
    ('crm_helpdesk.view_report_crm_helpdesk_filter', 'tradetested.view_report_crm_helpdesk_filter'),
    ('crm_helpdesk.view_report_crm_helpdesk_tree', 'tradetested.view_report_crm_helpdesk_tree'),
    ('crm_helpdesk.view_report_crm_helpdesk_graph', 'tradetested.view_report_crm_helpdesk_graph')
]

def stock_cleanup(env):
    # Use original sequence for stock operations

    env.cr.execute("UPDATE stock_picking_type set sequence_id = ( SELECT id from ir_sequence WHERE name = 'Picking OUT') WHERE name = 'Delivery Orders' and (SELECT id from ir_sequence WHERE name = 'Picking OUT')>0 ")
    env.cr.execute("UPDATE stock_picking_type set sequence_id = ( SELECT id from ir_sequence WHERE name = 'Picking INT') WHERE name = 'Internal Transfers' and (SELECT id from ir_sequence WHERE name = 'Picking INT') > 0")
    env.cr.execute("UPDATE stock_picking_type set sequence_id = ( SELECT id from ir_sequence WHERE name = 'Picking IN') WHERE name = 'Receptions' and (SELECT id from ir_sequence WHERE name = 'Picking IN') > 0")

    env.cr.execute("DELETE from ir_ui_view WHERE arch_db ilike '%onchange_template_id%' and model='mail.compose.message' ")

def sales_cleanup(env):

    env.cr.execute("DELETE from product_pricelist WHERE name='Default Purchase Pricelist' ")

def move_activity_logs(env):
    env.cr.execute("INSERT INTO res_activity_log (user_id, date, res_id, res_model, activity, value_before, value_after, move_id) SELECT user_id, date, product_id, 'product.product', activity, value_before, value_after, move_id from product_activity_log")
    env.cr.execute("INSERT INTO res_activity_log (user_id, date, res_id, res_model, activity, value_before, value_after) SELECT user_id, date, sale_id, 'sale.order', activity, value_before, value_after from sale_activity_log")
    env.cr.execute("INSERT INTO res_activity_log (user_id, date, res_id, res_model, activity, value_before, value_after) SELECT user_id, date, purchase_id, 'purchase.order', activity, value_before, value_after from purchase_activity_log")
    env.cr.execute("INSERT INTO res_activity_log (user_id, date, res_id, res_model, activity, value_before, value_after) SELECT user_id, date, helpdesk_id, 'crm.helpdesk', activity, value_before, value_after from crm_activity_log")
    env.cr.execute("INSERT INTO res_activity_log (user_id, date, res_id, res_model, activity, value_before, value_after) SELECT user_id, date, picking_id, 'stock.picking', activity, value_before, value_after from stock_picking_activity_log")

def crm_cleanup(env):

    env.cr.execute("DELETE from ir_ui_view where name='crm.segmentation.form' ")
    env.cr.execute("DELETE from ir_ui_view where name='crm.segmentation.tree' ")

def migrate_product(env):

    # Update Dropship products's Route
    env.cr.execute("""
        INSERT INTO stock_route_product (product_id, route_id) select product_tmpl_id, (select id from stock_location_route WHERE name = 'Drop Shipping') from product_product WHERE drop_ship = True;
    """)

    pass

    # Fields date_obsolete, volume container volume storage, State, moved from template to product

@openupgrade.migrate()
def migrate(env, version):
    #openupgrade.rename_xmlids(env.cr, _xmlid_renames)
    delete_old_module_views(env.cr, 'crm_helpdesk')
    delete_old_module_views(env.cr, 'account_full_reconcile') # gone



    stock_cleanup(env)
    sales_cleanup(env)
    move_activity_logs(env)
    crm_cleanup(env)