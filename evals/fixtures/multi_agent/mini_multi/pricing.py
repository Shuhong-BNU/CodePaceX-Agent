def total_price(unit_price: float, quantity: int, tax_rate: float) -> float:
    return round(unit_price * quantity * (1 + tax_rate), 2)
