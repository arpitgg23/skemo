"""Deal's own rules. This file is what an app developer writes by hand.

It runs inside the save's transaction: raising, or calling doc.error(), stops the save and
leaves the database as it was.
"""

from dhancha.hooks import DocumentHooks, ValidationError

WON_OR_LOST = ("Won", "Lost")


class Deal(DocumentHooks):
    def validate(self, doc):
        if doc["amount"] is not None and doc["amount"] < 0:
            doc.error("amount", "can't be negative")
        if doc["probability"] is not None and not 0 <= doc["probability"] <= 100:
            doc.error("probability", "must be between 0 and 100")
        if doc["stage"] == "Won" and doc["amount"] is None:
            raise ValidationError("A won deal needs an amount.", field="amount")

    def before_save(self, doc):
        """A deal that's finished isn't a maybe any more."""
        if doc["stage"] in WON_OR_LOST:
            doc["probability"] = 100 if doc["stage"] == "Won" else 0

    def after_save(self, doc):
        """Note the win on the deal's own notes, once, when it's first won."""
        became_won = doc["stage"] == "Won" and (doc.is_new or doc.previous["stage"] != "Won")
        if became_won:
            note = f"Won by {doc.user}."
            # Postgres needs the cast: it can't tell what type a bare parameter is here.
            doc.conn.exec_driver_sql(
                "UPDATE tab_deal SET notes = concat_ws(chr(10), notes, %s::text) WHERE name = %s",
                (note, doc.name),
            )
