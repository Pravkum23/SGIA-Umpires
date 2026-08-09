def is_admin_request(query_params):
    """The public route is the secure default; admin is opt-in via ?admin=1."""
    value = query_params.get("admin")
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) == "1"
