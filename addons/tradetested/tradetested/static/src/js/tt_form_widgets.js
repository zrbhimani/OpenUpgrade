odoo.define('tradetested.tt_form_widgets', function (require) {
"use strict";

var core = require('web.core');
var common = require('web.form_common');

    var FieldSerialized = common.AbstractField.extend(common.ReinitializeFieldMixin,{
        render_value: function() {
            this.$el.JSONView(this.get("value") || '{}', { collapsed: true, nl2br: true, recursive_collapser: true });
        },
    });

    var FieldSelectionColor = core.form_widget_registry.get('selection').extend({

        initialize_content: function() {
            this._super();
            this.colors = JSON.parse(this.node.attrs.colors)
        },

        render_value: function() {
            var values = this.get("values");
            values =  [[false, this.node.attrs.placeholder || '']].concat(values);
            var found = _.find(values, function(el) { return el[0] === this.get("value"); }, this);
            if (! found) {
                found = [this.get("value"), _t('Unknown')];
                values = [found].concat(values);
            }
            if (! this.get("effective_readonly")) {
                this.$().html(QWeb.render("FieldSelectionSelect", {widget: this, values: values}));
                this.$("select").val(JSON.stringify(found[0]));
            } else {
                this.$el.text(found[1]);
                this.$el.css( "color", this.colors[found[0]]);
            }
        },
    });

    var FieldRawHTML = core.form_widget_registry.get('text').extend({
        template: 'FieldTextraw_html',
        render_value: function()
        {
            this.$el.html(this.get('value'));
        }
    });

	var FieldStockIndicator = core.form_widget_registry.get('char').extend({
        template: 'FieldStockIndicator',
        render_value: function() {
            var val = this.get('value');
            if (val){
                var parts = val.split(',');
                var indicator = parts[0];
                var tooltip = parts[1];
                this.$el.find('img').attr('src', 'tradetested/static/src/img/ind_' + indicator +'.png').attr('title', tooltip);
            }
            else{
                //this.$el.remove();
            }
        },
	});

    core.form_widget_registry
        .add('serialized', FieldSerialized)
        .add('selection_color', FieldSelectionColor)
        .add('raw_html', FieldRawHTML)
        .add('stock_indicator', FieldStockIndicator)

});