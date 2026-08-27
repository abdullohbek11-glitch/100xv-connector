# -*- coding: utf-8 -*-
"""Bazadagi phone/mobile raqamlarini E.164 formatiga keltirish (bir martalik).

Preview — nechta yozuv o'zgaradi va nechtasi parse bo'lmaydi (qo'lda tuzatish uchun);
Apply — haqiqiy qayta yozadi.
"""
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

try:
    import phonenumbers
except ImportError:
    phonenumbers = None


def _to_e164(raw, region):
    """Faqat VALID E.164 qaytaradi (aks holda None) — noto'g'ri qayta-yozuvdan saqlaydi."""
    if not raw or not phonenumbers:
        return None
    try:
        num = phonenumbers.parse(str(raw), region)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return None


class LevelixNumberFix(models.TransientModel):
    _name = "levelix.number.fix"
    _description = "Normalize Phone Numbers (E.164)"

    fix_partners = fields.Boolean(string="Contacts (res.partner)", default=True)
    fix_leads = fields.Boolean(string="Leads (crm.lead)", default=True)
    country_id = fields.Many2one(
        "res.country", string="Default Country",
        default=lambda self: self.env.company.country_id,
        help="Used to parse numbers without a country code.",
    )
    result = fields.Text(string="Result", readonly=True)

    def _region(self):
        return (self.country_id.code or self.env.company.country_id.code or "UZ")

    def _scan(self):
        """(o'zgaradigan (record, field, old, new) ro'yxati, parse bo'lmaganlar soni)."""
        region = self._region()
        changes, unparsed = [], 0
        targets = []
        if self.fix_partners:
            targets.append(("res.partner", ["phone"]))
        if self.fix_leads:
            targets.append(("crm.lead", ["phone"]))
        for model, fnames in targets:
            domain = ["|"] * (len(fnames) - 1) + [(f, "!=", False) for f in fnames]
            for rec in self.env[model].search(domain):
                for f in fnames:
                    raw = rec[f]
                    if not raw:
                        continue
                    e164 = _to_e164(raw, region)
                    if e164 is None:
                        unparsed += 1
                    elif e164 != raw:
                        changes.append((rec, f, raw, e164))
        return changes, unparsed

    def action_preview(self):
        changes, unparsed = self._scan()
        lines = [_("Would change %s number(s); %s could not be parsed.") % (len(changes), unparsed)]
        for rec, f, old, new in changes[:40]:
            lines.append("  %s#%s.%s: %s → %s" % (rec._name, rec.id, f, old, new))
        if len(changes) > 40:
            lines.append(_("  … and %s more") % (len(changes) - 40))
        self.result = "\n".join(lines)
        return self._reopen()

    def action_apply(self):
        changes, unparsed = self._scan()
        for rec, f, _old, new in changes:
            rec.write({f: new})
        self.result = _("Done. Updated %s number(s); %s could not be parsed (left as-is).") % (
            len(changes), unparsed
        )
        _logger.info("Levelix number fix: updated %s, unparsed %s", len(changes), unparsed)
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "levelix.number.fix",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
