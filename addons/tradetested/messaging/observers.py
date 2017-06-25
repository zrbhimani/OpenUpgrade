from cli.receiver import Observer

class OrderImportObserver(Observer):
    queue = 'bus.odoo.orders'
    def run(self, env, body, message):
        env['tradetested.importers.order'].import_order(body)

class DeliveryOrderImportObserver(Observer):
    queue = 'bus.odoo.delivery_orders'
    def run(self, env, body, message):
        env['tradetested.importers.delivery_order'].import_delivery_order(body)

class ProductImportObserver(Observer):
    queue = 'magento.bus.products'
    def run(self, env, body, message):
        env['tradetested.importers.product'].import_product(body)
