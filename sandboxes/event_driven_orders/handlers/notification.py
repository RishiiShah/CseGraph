def on_order_created(payload: dict) -> dict:
    return {
        "type": "notification",
        "message": f"Order created for {payload['customer_id']}",
    }
