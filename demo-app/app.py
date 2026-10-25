def total(price, qty, tax_rate):
    subtotal = price * qty
    tax = subtotal * tax_rate
    return subtotal + tax
