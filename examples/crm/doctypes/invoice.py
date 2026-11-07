"""An invoice adds itself up: each line, then the subtotal, GST and total.

This is what child tables and hooks are for together — the person fills in the lines, and
the amounts are never typed by hand, so they can't disagree with each other.
"""

from decimal import ROUND_HALF_UP, Decimal

from skemo.hooks import DocumentHooks, ValidationError

PAISE = Decimal("0.01")


def money(amount: Decimal) -> Decimal:
    """Round to paise, the way an invoice is read: half a paisa rounds up."""
    return amount.quantize(PAISE, rounding=ROUND_HALF_UP)


class Invoice(DocumentHooks):
    def before_save(self, doc):
        subtotal = Decimal(0)
        for row in doc["items"]:
            row["amount"] = money(Decimal(str(row["qty"] or 0)) * (row["rate"] or Decimal(0)))
            subtotal += row["amount"]
        doc["subtotal"] = money(subtotal)
        doc["gst_amount"] = money(subtotal * Decimal(str(doc["gst_percent"] or 0)) / 100)
        doc["total"] = money(doc["subtotal"] + doc["gst_amount"])

    def validate(self, doc):
        if doc["status"] != "Draft" and not doc["items"]:
            raise ValidationError("An invoice needs at least one item before it's sent.", "items")
        for index, row in enumerate(doc["items"], start=1):
            if (row["qty"] or 0) <= 0:
                doc.error("items", f"row {index}: quantity must be more than zero")

    def before_delete(self, doc):
        if doc["status"] == "Paid":
            raise ValidationError("A paid invoice can't be deleted. Mark it cancelled instead.")
