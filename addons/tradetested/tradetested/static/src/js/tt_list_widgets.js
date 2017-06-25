odoo.define('tradetested.tt_list_widgets', function (require) {
"use strict";

var core = require('web.core');
var formats = require('web.formats');


    var SelectionColor = core.list_widget_registry.get('field').extend({
        init: function (id, tag, attrs) {
            this._super(id, tag, attrs)
            this.colors = this.colors ? JSON.parse(this.colors) : {};
            this.widget='selection'
        },

        _format: function (row_data, options) {
            var value = _.escape(formats.format_value(row_data[this.id].value, this, options.value_if_empty));
            return _.template("<span style='color:<%-color%>'><%-value%></a>")({color: this.colors[row_data[this.id].value], value: value});
        }
    });

    var TextTruncate = core.list_widget_registry.get('field').extend({
        format: function (row_data, options) {
            options = options || {};
            var attrs = {};
            if (options.process_modifiers !== false) {
                attrs = this.modifiers_for(row_data);
            }
            if (attrs.invisible) { return ''; }

            if (!row_data[this.id]) {
                return options.value_if_empty === undefined
                        ? ''
                        : options.value_if_empty;
            }
            return this._format_truncate(row_data, options);
        },
        _format_truncate: function (row_data, options) {
            return _.escape(formats.format_value(
                this.truncate(row_data[this.id].value, 100) , this, options.value_if_empty));
        },
        truncate: function (data, n){
            var isTooLong = data.length > n,
                s_ = isTooLong ? data.substr(0,data.substr(0,n-1).lastIndexOf(' ')) + ' …' : data;
            return s_;
        },
    });

	var StockIndicator = core.list_widget_registry.get('field').extend({
        _format: function(row_data, options) {
            var value = row_data[this.id].value;
            if (value) {
                var parts = value.split(',');
                var indicator = parts[0];
                var tooltip = parts[1];
                if (indicator!==''){
                    return _.template('<img title="<%-tooltip%>" src="tradetested/static/src/img/ind_<%-ind%>.png" width="16" height="16" style="margin-right:-4px">')({
                        tooltip: tooltip,
                        ind: indicator
                    });
                }
                else{
                    return _.template('<span />')({});
                }
            }
            return this._super(row_data, options);
        },
	});

    core.list_widget_registry
        .add('field.selection_color', SelectionColor)
        .add('field.text_truncate', TextTruncate)
        .add('field.stock_indicator', StockIndicator);

});