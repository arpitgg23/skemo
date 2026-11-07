# CRM — an example Dhancha app

A small app showing the layout every Dhancha app uses:

```
crm/
  app.json                 the app's name, title and version
  doctypes/
    customer.json          a DocType, the same JSON the builder saves
    deal.json
    deal.py                Deal's Python hooks: validation and computed values
    lead.json
```

Install it on a site and apply its DocTypes:

```bash
dhancha install-app examples/crm
```

After that, editing a JSON file and running `dhancha migrate` applies the change to every
site that has the app — that's how a change travels from your laptop to a customer's site.

`deal.py` shows the three things hooks are for:

- **validate** — refuse a save: a negative amount, a probability outside 0–100, a won deal
  with no amount.
- **before_save** — compute a value: a won deal is 100% and a lost one is 0%.
- **after_save** — react once, when a deal is first won.
