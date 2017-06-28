odoo.define('tradetested.web', function (require) {
"use strict";

    var core = require('web.core');
    var FormView = require('web.FormView');
    var common = require('web.form_common');
    var sidebar = require('web.Sidebar');
    var FieldReference = core.form_widget_registry.get('reference');
    var _t = core._t;


    var SidebarTT = sidebar.include({
        init: function(parent, options) {
            console.log('LOADED')
            this._super(parent, options);
            this.sections = options.sections || [
                {name: 'print', label: _t('Download')},
                {name: 'other', label: _t('Action')},
            ];
        },
    });

    FieldReference.include({

        render_value: function() {
            this.reference_ready = false;
            if (!this.get("effective_readonly")) {
                this.selection.set_value(this.get('value')[0]);
            }
            this.selection.set_value(this.get('value')[0]);
            this.selection.$el.css({'font-weight':'Bold', 'width':'140px', 'padding-right':'8px'});
            this.m2o.field.relation = this.get('value')[0];
            this.m2o.set_value(this.get('value')[1]);
            this.m2o.$el.toggle(!!this.get('value')[0]);
            this.m2o.$el.css('padding-left','8px');
            this.reference_ready = true;

            if (this.view.dataset.model == 'crm.helpdesk'){
//                $ctx = this.field_manager.build_eval_context();
//                $object = $ctx.__contexts[$ctx.__contexts.length - 1];
//                this.m2o.node.attrs.context = {
//                    'case_order_id': $object.order_id,
//                    'case_partner_id': $object.partner_id
//                };
            }
        },

        initialize_content: function() {
            this._super.apply(this, arguments);
            this.$el.css({'margin-bottom': '0px'});
            this.selection.$el.removeClass('o_form_field');
            this.selection.$el.css({'margin-bottom':'9px'})
            this.m2o.$el.css({'padding-bottom':'5px', 'margin-left':'12px','border-left':'1px solid #ccc'});
        },

    });

    FormView.include({

        do_show: function (options) {
            if (this.dataset.model === 'sale.order'){
                if (this.dataset.index === null ){
                    this.popup_create();
                }
                this.$el.off("click", '.oe_customer_quick_create').on('click', '.oe_customer_quick_create', this.popup_create.bind(this));
            }
            return this._super(options);
        },

        popup_create: function() {
            var pop = new common.FormViewDialog(false, {
                res_model: 'res.partner',
                context: {'customer_quick_create_view': true, 'ref': "compound_context", 'form_view_ref':'tradetested.view_partner_from_sale_order'},
                title: "Create: Customer",
                disable_multiple_selection: true,
            }).open();
            pop.on('create_completed', this, function(id) {
                this.datarecord.partner_id = id;
                this.fields.partner_id.set_value(id);
            });
        },

    });


});