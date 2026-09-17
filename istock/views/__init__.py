def register_blueprints(app):
    from ..auth import bp as auth_bp
    from .analytics import bp as analytics_bp
    from .inventory import bp as inventory_bp
    from .main import bp as main_bp
    from .orders import bp as orders_bp
    from .partners import bp as partners_bp

    for bp in (auth_bp, main_bp, inventory_bp, partners_bp, orders_bp, analytics_bp):
        app.register_blueprint(bp)
